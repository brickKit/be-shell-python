"""阶段四 Task 7：真的把 ``infra-print`` 这个真实、未经任何修改的组件
模块装进 ``shell.run``，不是假模块——同 ``be-shell-go``
``real_modules_test.go`` 的判断（真实迁移、真实健康检查、真实权限
中间件 401），Python 这边的对应验证。

⚠️ ``infra-print`` 的 ``Module.migrations_dir`` 是相对 CWD 的
``Path("migrations")``（见 ``shell/run.py`` 顶部说明的已知缺口），不是
编译进包里的数据——真实部署时 Dockerfile 会 ``git clone`` 对应 tag 把
``migrations/`` 目录放到 ``WORKDIR`` 根（见 Dockerfile 的
``INFRA_PRINT_VERSION``）。这里用同一个 tag 现 clone 一份到临时目录、
``monkeypatch.chdir`` 过去，不依赖"本机恰好在同一个 monorepo 里检出了
``components/infra/print``"这个环境假设——CI 只会检出 ``be-shell-python``
自己这一个仓库。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from shell import Config, ModuleSpec, run

# 必须与 Dockerfile 的 INFRA_PRINT_VERSION、pyproject.toml 的 infra-print
# 依赖 tag 三处保持一致——见 Dockerfile 里那条"两处都改，不能只改一处"
# 的既有提醒。
_INFRA_PRINT_TAG = "v1.0.5"


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


async def _wait_healthy(port: int, path: str = "/") -> None:
    import asyncio

    url = f"http://127.0.0.1:{port}{path}"
    async with httpx.AsyncClient() as client:
        for _ in range(300):
            try:
                resp = await client.get(url, timeout=0.1)
                if resp.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.02)
    pytest.fail(f"{url} 在 6 秒内没有开始正常响应")


@pytest.fixture
def infra_print_migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """clone 真实的 infra-print migrations/ 到临时目录，chdir 过去——
    模拟真机部署时 Dockerfile 的 CWD 布局，不是伪造出来的替代数据。
    """
    clone_dir = tmp_path / "infra-print"
    subprocess.run(
        [
            "git",
            "clone",
            "--depth",
            "1",
            "--branch",
            _INFRA_PRINT_TAG,
            "https://github.com/brickKit/infra-print.git",
            str(clone_dir),
        ],
        check=True,
        capture_output=True,
    )
    migrations_dir = clone_dir / "migrations"
    assert migrations_dir.is_dir(), "clone 下来的 infra-print 没有 migrations/ 目录"
    monkeypatch.chdir(clone_dir)
    return migrations_dir


async def test_infra_print真实模块合并进外壳后迁移真实跑通且路由真实fail_closed(
    infra_print_migrations: Path,
) -> None:
    """py-render 外壳目前唯一的模块——验证的是"这一个真实模块能不能在
    外壳骨架里正常跑"，不是"骨架本身对不对"（那是 Task 3 假模块测试的
    职责，本文件不重复）。
    """
    # 延迟 import：必须在 chdir 到 clone 目录、且 sys.path 正确之后才 import，
    # 但 app.module 是从已安装的 site-packages 里读代码（pip 装的那份），
    # 只有它内部 Path("migrations") 这一处相对路径解析吃 CWD——所以 import
    # 本身在哪个 CWD 下都一样，这里沿用模块级 import 风格即可，不需要
    # 延迟到 fixture 之后。
    from app.module import create_module

    http_port = _free_port()
    health_port = _free_port()

    spec = ModuleSpec(
        component_id="infra/print",
        component_version="1.0.5",
        # infra-print 没有必填 config 项（component.yaml 的 pgSchema 走
        # 默认值 "infra_print"），空 env 就是它真实部署时的样子。
        env={},
        http_port=http_port,
        new_module=create_module,
        schema="infra_print",
    )
    cfg = Config(
        shell_name="be-shell-python-test",
        pg_dsn=_pg_dsn(),
        nats_url=_nats_url(),
        health_port=health_port,
        modules=[spec],
    )

    import asyncio

    stop_event = asyncio.Event()
    run_task = asyncio.create_task(run(cfg, stop_event))
    try:
        await _wait_healthy(health_port)
        await _wait_healthy(http_port, "/infra/print/templates")

        async with httpx.AsyncClient() as client:
            # 真机跑出来的实际行为（不是照抄 be-shell-go 的猜测）：besdk-python
            # 的判据是"iamJwksUrl 没配就直接 403"，比"有没有带 token"更早
            # 判定——这条 Config 没给 iam_jwks_url/authz_bundle_url（本条
            # 用例的范围是"迁移+路由真的跑通"，不是"完整鉴权链路"，那是
            # Task 9 的范围），所以是 403 而不是 401。
            resp = await client.get(f"http://127.0.0.1:{http_port}/infra/print/templates")
            assert resp.status_code == 403, f"期望 403，实际 {resp.status_code}: {resp.text}"
            assert "iamJwksUrl" in resp.text
    finally:
        stop_event.set()
        await asyncio.wait_for(run_task, timeout=10)
