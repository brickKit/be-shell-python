"""be-shell-python 的进程入口——读平台原生注入的
``BRICKKIT_SERVED_MEMBERS_CONFIG``，把 ``componentId`` 字符串映到真实的
Python 源码 import——判断与 ``be-shell-go`` 的 ``cmd/shell/main.go`` 逐一
对应（同一套机制的 Python 版，不是另一套设计）。

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

⚠️ 阶段四附加 Task 0.6（brickKit v0.4.2）：``SHELL_CONFIG_JSON`` +
``be-ops shell-config`` 那一整套"我们自己手工生成、手工贴进
brickkit.yaml"的机制整个退休——平台原生新增
``BRICKKIT_SERVED_MEMBERS_CONFIG``，在算 ``BRICKKIT_SERVED_MEMBERS`` 的
同一处代码里，把每个真的被这个外壳收编的成员的完整装配数据
（``componentId``/``version``/``httpPort``/``extraPorts``/合并后的
``config``）直接原生注入，不再需要我们自己起一个命令行工具算一遍、
brickkit.yaml 一改版本号/config/servedBy 归属就手工维护的数据就会过期
这整类坑因此从设计上消失（真机复发过两次，见装配仓库
``docs/dev/架构复盘-servedBy落地后的自有改进空间.md`` 发现二）。也因此
不再需要按 ``BRICKKIT_SERVED_MEMBERS`` 单独筛一遍——这份新变量本身就
已经是"这次真的被收编"的成员集合，不是"这个外壳理论上可能收编的全部
成员"。

⚠️ ``config`` 里的键是原始 configSchema 驼峰 key（brickKit 刻意不做
大写下划线转换，交给外壳实现者自己处理，见 brickKit 文档
``shell-implementers-guide`` 原文）——``_config_env_var_name`` 把它转成
``besdk.Config`` 内部查找用的 ``SCREAMING_SNAKE_CASE``，算法与
``be-shell-go`` 的 ``configEnvVarName``、brickKit 自己的
``internal/inject.EnvVarName`` 逐字对应。
"""

from __future__ import annotations

import asyncio
import json
import os
import re
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
    还没被平台判定为"这个外壳这次真的要装的模块"之前，不应该在 import
    这个文件本身的时候就强制拉起它的依赖链（weasyprint 等）。真的有
    第二个 Python 组件时，在这里追加一行，不改其余逻辑。
    """
    from app.module import create_module as infra_print_create_module

    _MODULE_REGISTRY["infra/print"] = infra_print_create_module


_ENV_VAR_NAME_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _config_env_var_name(key: str) -> str:
    """把一个原始 configSchema 驼峰 key 转成 ``SCREAMING_SNAKE_CASE``
    ——跟 ``besdk.Config`` 内部查找配置项时用的转换规则（camelCase →
    大写下划线）逐字对应，也是 brickKit 自己
    ``internal/inject.EnvVarName`` 的算法，这里原样复刻（同
    ``be-shell-go`` 的 ``configEnvVarName``）。
    """
    normalized = key.replace("-", "_").replace(".", "_").replace(" ", "_")
    return _ENV_VAR_NAME_BOUNDARY_RE.sub("_", normalized).upper()


def _sanitize_served_members_config(raw: str) -> str:
    """修复 docker compose 自己对 ``${VAR}`` 做全文本替换时、在 JSON
    字符串内部留下的原始控制字符——判断逐一对应 ``be-shell-go`` 的
    ``sanitizeServedMembersConfig``。

    真机 ``brickkit up`` 复现出：brickKit 生成 ``BRICKKIT_SERVED_
    MEMBERS_CONFIG`` 这份 JSON 的那一刻，字符串内部不会有任何未转义的
    控制字符；但密钥类 config 值在 ``brickkit.yaml`` 里写的是
    ``${VAR}`` 占位符，docker compose 读取生成好的
    ``docker-compose.yaml`` 时会对整份文件按纯文本做 ``${VAR}``
    替换，不知道某个 ``${VAR}`` 恰好嵌在这份 JSON 字符串内部——真实
    密钥（PEM 私钥）自带原始换行符，替换进去就在"合法 JSON 字符串内部
    绝不会自己出现"的位置制造出裸控制字符。

    只转义**字符串内部**的控制字符，不能不分场合整段替换——字符串外部
    的裸换行/空白本来就是合法 JSON，用一个只关心"现在在不在字符串
    里面"的最小状态机（遇到未转义的 ``"`` 切换状态，``\\`` 时跳过下一个
    字符防止误判转义序列）来分辨。
    """
    out: list[str] = []
    in_string = False
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if in_string and ch == "\\" and i + 1 < n:
            out.append(raw[i : i + 2])
            i += 2
            continue
        if ch == '"':
            in_string = not in_string
            out.append(ch)
            i += 1
            continue
        if in_string and ch in ("\n", "\r", "\t"):
            out.append({"\n": "\\n", "\r": "\\r", "\t": "\\t"}[ch])
            i += 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _build_modules() -> list[ModuleSpec]:
    raw = os.environ.get("BRICKKIT_SERVED_MEMBERS_CONFIG")
    if raw is None:
        raise RuntimeError(
            "BRICKKIT_SERVED_MEMBERS_CONFIG 未设置——这个容器看起来不是被 servedBy 正常收编启动的"
            "（平台总会至少注入一个 [] 空数组），检查是不是手动 docker run 漏传了这个变量"
        )

    try:
        members = json.loads(_sanitize_served_members_config(raw))
    except ValueError as exc:
        raise RuntimeError(f"解析 BRICKKIT_SERVED_MEMBERS_CONFIG 失败: {exc}") from exc

    _register_known_modules()

    specs: list[ModuleSpec] = []
    for m in members:
        component_id = m["componentId"]
        new_module = _MODULE_REGISTRY.get(component_id)
        if new_module is None:
            raise RuntimeError(
                f"组件 {component_id} 在 BRICKKIT_SERVED_MEMBERS_CONFIG 里，"
                "但 _MODULE_REGISTRY 没有登记它的真实 create_module——是不是漏了给它加 import"
            )

        config: dict[str, str] = m.get("config") or {}
        # pgSchema 要在转换成 SCREAMING_SNAKE_CASE 之前，按原始驼峰 key
        # 取——它是每个组件都有的既定配置项，不是可选字段。
        schema = config.get("pgSchema", "")
        if not schema:
            raise RuntimeError(
                f"组件 {component_id} 的 config 里没有 pgSchema——"
                "BRICKKIT_SERVED_MEMBERS_CONFIG 的数据看起来不完整"
            )

        env = {_config_env_var_name(k): v for k, v in config.items()}
        extra_ports = {p["name"]: p["port"] for p in (m.get("extraPorts") or [])}

        specs.append(
            ModuleSpec(
                component_id=component_id,
                component_version=m["version"],
                env=env,
                http_port=m["httpPort"],
                new_module=new_module,
                extra_ports=extra_ports,
                schema=schema,
            )
        )
    return specs


async def _main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    shell_name = os.environ.get("SHELL_NAME")
    if not shell_name:
        raise RuntimeError("SHELL_NAME 未设置（py-render，component.yaml 的 shellName 项）")

    modules = _build_modules()

    pg_dsn = besdk.build_pg_dsn("be-shell-python-" + shell_name)

    # ⚠️ SHELL_HEALTH_PORT 同 be-shell-go 的既有判据：不是平台注入的
    # （外壳根本不是 brickKit 组件）——外壳自己的 component.yaml 声明这个
    # 端口，值写在 brickkit.yaml 该外壳组件的 config 块里，这里只负责读。
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
