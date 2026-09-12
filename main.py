"""be-shell-python 的进程入口——目前只是骨架（阶段四计划 Task 3）：
``modules`` 列表故意留空，本阶段只准备装 1 个模块（``infra-print``），
但要等它正式作为依赖被 ``pyproject.toml`` 声明、且 ``be-ops`` 产出 4/7
落地后才会填（Task 5/6 是 Go 组件那一批，本仓库对应的那一步暂未编号，
留给出档时一并处理）。
"""

from __future__ import annotations

import asyncio
import os
import signal

import besdk

from shell import Config, run


async def _main() -> None:
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_event.set)

    pg_dsn = besdk.build_pg_dsn("be-shell-python")

    # ⚠️ SHELL_HEALTH_PORT 同 be-shell-go 的既有判据：不是平台注入的
    # （外壳根本不是 brickKit 组件）——be-ops 产出 8（shell-compose.yml）
    # 生成时会把这个端口写进 healthcheck 配置，这里只负责读。
    health_port = int(os.environ.get("SHELL_HEALTH_PORT", "0")) or 18889

    cfg = Config(
        shell_name="be-shell-python",
        otel_base_url=os.environ.get("OTEL_BASE_URL", ""),
        pg_dsn=pg_dsn,
        nats_url=besdk.build_nats_url(),
        health_port=health_port,
        # ⚠️ 阶段四本仓库对应任务之前，这里故意是空的——见模块顶部说明。
        modules=[],
    )

    await run(cfg, stop_event)


if __name__ == "__main__":
    asyncio.run(_main())
