"""be-shell-python 的进程入口——阶段四 Task 7：读 ``be-ops`` 产出 4
（``SHELL_CONFIG_JSON``）/产出 7（``SHELL_ENV_JSON``）两份数据文件，按
``SHELL_NAME`` 挑出自己要装的外壳，把 ``componentId`` 字符串映到真实的
Python 源码 import——判断与 ``be-shell-go`` 的 ``cmd/shell/main.go``
逐一对应（同一套机制的 Python 版，不是另一套设计）。

本仓库目前只对应 1 个外壳实例（``py-render``，唯一成员
``infra/print``），但装配方式仍然走同一套"数据驱动配置、代码驱动装配"
的模式——``ModuleSpec.new_module`` 不能从数据文件动态加载（Python 的
import 系统没有编译期强制，但判断与 Go 版一致：具体要跑哪个模块的
``create_module``，必须是这个文件里静态写死的 import，不能凭字符串
反射着导入），真的有第二个 Python 组件加入 ``py-render`` 时，只需要在
``_MODULE_REGISTRY`` 里加一行，不需要重新设计这一层。
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


def _load_json(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _build_modules(shell_name: str) -> list[ModuleSpec]:
    config_path = os.environ.get("SHELL_CONFIG_JSON")
    env_path = os.environ.get("SHELL_ENV_JSON")
    if not config_path or not env_path:
        raise RuntimeError("SHELL_CONFIG_JSON/SHELL_ENV_JSON 未设置（be-ops shell-config/shell-env 的产出路径）")

    config_shells = _load_json(config_path)
    env_shells = _load_json(env_path)

    config_shell = next((s for s in config_shells if s["name"] == shell_name), None)
    if config_shell is None:
        raise RuntimeError(
            f"shell-config.json 里没有外壳 {shell_name!r}（是不是 brickkit.yaml 还没原子式切换，或者 SHELL_NAME 拼错了）"
        )
    env_shell = next((s for s in env_shells if s["Name"] == shell_name), None)
    if env_shell is None:
        raise RuntimeError(
            f"shell-env.json 里没有外壳 {shell_name!r}（是不是 be-ops shell-env 生成时这个外壳还没原子式切换完，被跳过了）"
        )
    env_by_component = {m["ComponentID"]: m["Env"] for m in env_shell["Modules"]}

    _register_known_modules()

    specs: list[ModuleSpec] = []
    for m in config_shell["modules"]:
        component_id = m["componentId"]
        new_module = _MODULE_REGISTRY.get(component_id)
        if new_module is None:
            raise RuntimeError(
                f"组件 {component_id} 在 shell-config.json 里，但 _MODULE_REGISTRY 没有登记它的真实 create_module——是不是漏了给它加 import"
            )
        env = env_by_component.get(component_id)
        if env is None:
            raise RuntimeError(
                f"组件 {component_id} 在 shell-config.json 里，但 shell-env.json 的外壳 {shell_name!r} 下找不到它对应的环境变量"
            )
        specs.append(
            ModuleSpec(
                component_id=component_id,
                component_version=m["version"],
                env=env,
                http_port=m["httpPort"],
                new_module=new_module,
                extra_ports=m.get("extraPorts") or {},
                schema=m["schema"],
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
        raise RuntimeError("SHELL_NAME 未设置（py-render，见 shell-compose.yml）")

    modules = _build_modules(shell_name)

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
