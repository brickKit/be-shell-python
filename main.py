"""be-shell-python 的进程入口——读 ``be-ops`` 产出 4（``SHELL_CONFIG_JSON``），
按 ``SHELL_NAME`` 挑出自己要装的外壳，再按平台原生注入的
``BRICKKIT_SERVED_MEMBERS`` 筛出这次真的被 ``servedBy`` 收编、活着的
成员，把 ``componentId`` 字符串映到真实的 Python 源码 import——判断与
``be-shell-go`` 的 ``cmd/shell/main.go`` 逐一对应（同一套机制的 Python
版，不是另一套设计）。

本仓库目前只对应 1 个外壳实例（``py-render``，唯一成员
``infra/print``），但装配方式仍然走同一套"数据驱动配置、代码驱动装配"
的模式——``ModuleSpec.new_module`` 不能从数据文件动态加载（Python 的
import 系统没有编译期强制，但判断与 Go 版一致：具体要跑哪个模块的
``create_module``，必须是这个文件里静态写死的 import，不能凭字符串
反射着导入），真的有第二个 Python 组件加入 ``py-render`` 时，只需要在
``_MODULE_REGISTRY`` 里加一行，不需要重新设计这一层。

⚠️ 阶段四附加 Task 0.2/0.3：原来还要读 ``be-ops`` 产出 7
（``SHELL_ENV_JSON``）拿每个模块自己的 env——servedBy 落地后这份数据并
进了产出 4 的 ``config`` 字段，``SHELL_ENV_JSON`` 整个退休。完整调研
过程见装配仓库 ``docs/plans/04b-验证记录.md`` Task 0.2。

⚠️ 阶段四附加 Task 0.4：``SHELL_CONFIG_JSON`` 从"文件路径"改成了"内容
本身"——servedBy 外壳没有自己的 component.yaml 之外的任何东西可以挂载
（brickKit 的 manifest 模型没有 volumes 字段），"文件路径 + 挂载卷"这
条路在没有 volumes 的世界里走不通。现在这个环境变量的值直接是
``be-ops shell-config --shell py-render`` 打印出的、这一个外壳自己的
modules 数组（compact JSON），跟 infra/authz 的 permissionCatalog 是
同一种模式——写死在 brickkit.yaml 该外壳组件的 config.shellConfigJson
里，源头数据变了就重新跑一次那条命令、手动贴回去。也因此不再需要
"按 SHELL_NAME 挑外壳"这一步——内容从生成的那一刻起就已经只属于这一个
外壳。
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
from typing import TYPE_CHECKING

import besdk

from shell import Config, ModuleSpec, run

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from besdk import Module, Runtime

# 唯一"componentId 字符串 → 真实 Python 源码 import"的静态映射——同
# be-shell-go 的 moduleRegistry。
_MODULE_REGISTRY: dict[str, "Callable[[Runtime], Awaitable[Module]]"] = {}


def _register_known_modules() -> None:
    """延迟到函数内部 import，不在模块顶层——``app`` 包（infra-print）
    还没被 be-ops 产出 4 认领为"这个外壳要装的模块"之前，不应该在
    import 这个文件本身的时候就强制拉起它的依赖链（weasyprint 等）。
    真的有第二个 Python 组件时，在这里追加一行，不改其余逻辑。
    """
    from app.module import create_module as infra_print_create_module

    _MODULE_REGISTRY["infra/print"] = infra_print_create_module


_VERSIONED_NAME_RE_CHARS = str.maketrans({"/": "-", ".": "-"})


def _versioned_service_name(component_id: str, version: str) -> str:
    """与 brickKit 自己推导服务名的算法逐字对应（总纲 §2.1："/ → -、
    . → -、全部小写，再接精确版本号"）——"mdm/customer"@"1.0.7" →
    "mdm-customer-1-0-7"，跟 ``BRICKKIT_SERVED_MEMBERS`` 里的写法逐字
    一致。同 ``be-shell-go`` 的 ``versionedServiceName``。
    """
    return f"{component_id}-{version}".translate(_VERSIONED_NAME_RE_CHARS).lower()


def _served_member_set() -> set[str]:
    """读 ``BRICKKIT_SERVED_MEMBERS``（平台原生注入，servedBy 外壳"这次
    真的被收编、活着"的成员清单，逗号分隔的版本化服务名）。

    ⚠️ 必须区分"变量不存在"与"变量是空字符串"——不存在意味着这个容器
    可能不是被 servedBy 正常收编启动的（平台总会至少注入一个空字符串，
    真的读不到通常说明是手动 docker run 忘了传，必须报错，不能悄悄
    退化成"全部实例化"这类旧行为）；空字符串是合法状态，意味着这次
    没有任何成员被收编，应该装出 0 个模块。同 ``be-shell-go`` 的
    ``servedMemberSet``（Go 用 ``os.LookupEnv``，Python 用
    ``sentinel not in os.environ`` 达到同样的区分效果）。
    """
    if "BRICKKIT_SERVED_MEMBERS" not in os.environ:
        raise RuntimeError(
            "BRICKKIT_SERVED_MEMBERS 未设置——这个容器看起来不是被 servedBy 正常收编启动的"
            "（平台总会至少注入一个空字符串），检查是不是手动 docker run 漏传了这个变量"
        )
    raw = os.environ["BRICKKIT_SERVED_MEMBERS"]
    if not raw:
        return set()
    return {name.strip() for name in raw.split(",")}


def _build_modules() -> list[ModuleSpec]:
    raw = os.environ.get("SHELL_CONFIG_JSON")
    if not raw:
        raise RuntimeError(
            "SHELL_CONFIG_JSON 未设置（be-ops shell-config --shell <name> 的产出，"
            "应该是这个外壳自己的 modules 数组）"
        )
    served = _served_member_set()

    try:
        modules = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError(f"解析 SHELL_CONFIG_JSON 失败: {exc}") from exc

    _register_known_modules()

    matched: set[str] = set()
    specs: list[ModuleSpec] = []
    for m in modules:
        component_id = m["componentId"]
        name = _versioned_service_name(component_id, m["version"])
        if name not in served:
            # SHELL_CONFIG_JSON 列的是"这个外壳理论上有哪些成员"，不是
            # "这次都被收编了"——没在 BRICKKIT_SERVED_MEMBERS 里的成员
            # 这次没被平台收编，正常跳过，不是错误。
            continue
        matched.add(name)
        new_module = _MODULE_REGISTRY.get(component_id)
        if new_module is None:
            raise RuntimeError(
                f"组件 {component_id} 在 SHELL_CONFIG_JSON 里，但 _MODULE_REGISTRY 没有登记它的真实 create_module——是不是漏了给它加 import"
            )
        specs.append(
            ModuleSpec(
                component_id=component_id,
                component_version=m["version"],
                env=m.get("config") or {},
                http_port=m["httpPort"],
                new_module=new_module,
                extra_ports=m.get("extraPorts") or {},
                schema=m["schema"],
            )
        )

    missing = served - matched
    if missing:
        raise RuntimeError(
            f"BRICKKIT_SERVED_MEMBERS 里有 SHELL_CONFIG_JSON 找不到的成员：{sorted(missing)}"
            "（是不是 brickkit.yaml 改完之后忘了重新跑 be-ops shell-config --shell 把新字符串贴回 config.shellConfigJson）"
        )
    return specs


async def _main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    shell_name = os.environ.get("SHELL_NAME")
    if not shell_name:
        raise RuntimeError("SHELL_NAME 未设置（py-render，见 shell-compose.yml）")

    modules = _build_modules()

    pg_dsn = besdk.build_pg_dsn("be-shell-python-" + shell_name)

    # ⚠️ SHELL_HEALTH_PORT 同 be-shell-go 的既有判据：不是平台注入的
    # （外壳根本不是 brickKit 组件）——be-ops 产出 8（shell-compose.yml）
    # 生成时会把这个端口写进 healthcheck 配置，这里只负责读。
    health_port = int(os.environ.get("SHELL_HEALTH_PORT", "0")) or 18889

    cfg = Config(
        shell_name="be-shell-python-" + shell_name,
        otel_base_url=os.environ.get("OTEL_BASE_URL", ""),
        pg_dsn=pg_dsn,
        nats_url=besdk.build_nats_url(),
        iam_jwks_url=os.environ.get("IAM_JWKS_URL", ""),
        authz_bundle_url=os.environ.get("AUTHZ_BUNDLE_URL", ""),
        health_port=health_port,
        modules=modules,
    )

    await run(cfg, stop_event)


if __name__ == "__main__":
    asyncio.run(_main())
