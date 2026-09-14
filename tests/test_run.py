"""``shell.run`` 的空转验证——阶段四计划 Task 3 明确要求"先不装任何
真实模块"，这里用假模块验证外壳骨架本身，不依赖任何真实组件仓库。

需要真实可达的 Postgres（``TEST_PG_DSN``，同 ``be-sdk-python`` 既有
判据未设置就跳过）与 NATS（``TEST_NATS_URL``，默认 ``nats://127.0.0.1:4222``）
——``asyncpg.create_pool`` 与 ``run_standalone`` 一样默认会真的建立
连接，不是懒加载，跳过而不是放宽断言。
"""

from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from fastapi import FastAPI

from shell import Config, ModuleSpec, run
from besdk import Module


def _pg_dsn() -> str:
    dsn = os.environ.get("TEST_PG_DSN")
    if not dsn:
        pytest.skip("未设置 TEST_PG_DSN，跳过（CI 里必须设）")
    return dsn


def _nats_url() -> str:
    return os.environ.get("TEST_NATS_URL", "nats://127.0.0.1:4222")


def _free_port() -> int:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _fake_module_spec(component_id: str, port: int, *, start=None) -> ModuleSpec:
    async def new_module(rt) -> Module:  # noqa: ANN001 - rt 类型是 besdk.Runtime，这里不需要具体用到
        app = FastAPI()

        @app.get("/")
        async def _root() -> dict[str, bool]:
            return {"ok": True}

        return Module(asgi_app=app, start=start)

    return ModuleSpec(
        component_id=component_id,
        component_version="0.0.1",
        env={},
        http_port=port,
        new_module=new_module,
    )


async def _wait_healthy(port: int) -> None:
    url = f"http://127.0.0.1:{port}/"
    async with httpx.AsyncClient() as client:
        for _ in range(200):
            try:
                resp = await client.get(url, timeout=0.1)
                if resp.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.01)
    pytest.fail(f"端口 {port} 在 2 秒内没有开始正常响应")


async def test_两个假模块共享一个进程且都能真的响应HTTP() -> None:
    """阶段四外壳工程最核心的一条断言：验证外壳骨架本身能把两个模块的
    HTTP handler 都真的起来、stop_event 被 set 后都优雅退出。
    """
    port1, port2 = _free_port(), _free_port()
    cfg = Config(
        shell_name="be-shell-python-test",
        pg_dsn=_pg_dsn(),
        nats_url=_nats_url(),
        modules=[
            _fake_module_spec("fake/one", port1),
            _fake_module_spec("fake/two", port2),
        ],
    )
    stop_event = asyncio.Event()

    run_task = asyncio.create_task(run(cfg, stop_event))
    await _wait_healthy(port1)
    await _wait_healthy(port2)

    stop_event.set()
    await asyncio.wait_for(run_task, timeout=5)


async def test_HealthPort独立于任何模块自己响应() -> None:
    """外壳自己的健康检查端口（设计书 §13.6"一个容器只有一个 probe"，
    但这个 probe 不属于任何一个模块）即使配了 0 个真实模块也能正常
    响应——shell-compose.yml 的健康检查探的是这个端口，不是任意挑一个
    模块的端口。
    """
    health_port = _free_port()
    cfg = Config(
        shell_name="be-shell-python-test",
        pg_dsn=_pg_dsn(),
        nats_url=_nats_url(),
        health_port=health_port,
        modules=[],
    )
    stop_event = asyncio.Event()

    run_task = asyncio.create_task(run(cfg, stop_event))
    await _wait_healthy(health_port)

    stop_event.set()
    await asyncio.wait_for(run_task, timeout=5)


async def test_单模块Start里异常不崩溃整个进程只是优雅退出并清楚指出是哪个模块() -> None:
    """写 be-shell-go 骨架时发现的同一类真实缺口，在这里同样验证：
    ``Module.start`` 里的一次未捕获异常必须能被外壳干净地记录下来
    （带着 ``module_component_id``），且 ``run`` 本身正常返回，不会让
    整个测试进程崩溃。
    """

    async def _bad_start() -> None:
        raise RuntimeError("模拟模块自己代码里的一个真实 bug")

    port = _free_port()
    cfg = Config(
        shell_name="be-shell-python-test",
        pg_dsn=_pg_dsn(),
        nats_url=_nats_url(),
        modules=[_fake_module_spec("fake/panics", port, start=_bad_start)],
    )

    # 不传 stop_event：run() 自己会在 Start() 异常导致 asyncio.wait
    # 命中 FIRST_COMPLETED 之后自然收尾并返回，不需要外部再 set 一次。
    await asyncio.wait_for(run(cfg), timeout=5)


# ⚠️ 原来这里有 test_export_dependency_endpoints_真机复现/
# test_export_dependency_endpoints_同一个外壳内不一致时报错 两条用例，
# 测的是同 be-shell-go 阶段四 Task 9 真机复现的同一个 bug：
# besdk.endpoint() 读 os.environ 不是 rt.config，ModuleSpec.env 必须
# 额外导出到进程环境才能被 SystemClient/UserClient 看到。servedBy 落地
# 后（阶段四附加 Task 0.2/0.3）这一步整个不需要了——brickKit 自己在
# 生成阶段就把 *_ENDPOINT 类变量直接合并进外壳容器自己的 os.environ，
# 本包一启动就已经看得见，_export_dependency_endpoints 函数与这两条
# 测试一并删除。完整历史留在 README.md，不随代码一起消失。
