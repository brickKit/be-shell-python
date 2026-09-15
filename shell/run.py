"""be-shell-python 的全部装配逻辑——``internal/shell.Run``
（be-shell-go）的 Python 版，同一套判断：复用 ``besdk`` 已经在
``run_standalone``（单模块场景）里验证过的
``new_shell_runtime``/``init_shell_authz``/``serve_http``/
``serve_extra_port``，不重新实现一遍（阶段三踩坑记录 A4g 的教训：一条
只有小范围调用方走过的构造路径，跟生产真正走的那条路径不是同一条，
就是真实 bug 藏身的地方）。

⚠️ **编排方式故意不用 ``asyncio.TaskGroup``，跟 ``_run_standalone_async``
保持同一套习惯**：``besdk.serve_http``/``serve_extra_port`` 的既有签名
是"传一个 ``stop_event``，等它被 set 之后自己优雅退出"（不是靠外部
``.cancel()``），``Module.start`` 的既有约定则是"取消时必须返回"（靠
``.cancel()``）——两种停止方式混着用，``run_standalone`` 早就用
``asyncio.wait(..., FIRST_COMPLETED)`` + 手动 ``stop_event.set()`` +
对 pending 任务 ``.cancel()`` 解决了。外壳只是把"1 个模块的任务集合"
换成"N 个模块的任务集合拼在一起"，编排逻辑不应该另起一套。

迁移执行用 ``yoyo-migrations`` 的 Python API，直接吃每个模块
``Module.migrations_dir``（``Path``）——同 ``infra-print`` 自己
``backend/app/migrate.py`` 的既有判据：schema 隔离走 libpq 的
``options=-csearch_path=<schema>`` 连接参数，不 shell 出去调 CLI。

⚠️ **已知的、故意留到阶段五/六的缺口**：Python 组件的 ``migrations_dir``
是一个相对 CWD 的 ``Path``（不是 Go 版那种编译进二进制的 ``fs.FS``，
Python 的 import 系统没有对应的"路径不能越出包目录"限制，各组件也没有
这个约定）——本阶段外壳只装 1 个 Python 模块（``infra-print``），不会
撞见"两个模块的相对路径都解析到同一个 CWD"这个问题；阶段五/六真的有
第二个 Python 组件时，这里需要先解决"每个模块的 migrations_dir 到底
相对什么路径解析"，不能假设现在这份实现直接够用。
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

import asyncpg
import nats
from yoyo import get_backend, read_migrations

import besdk
from besdk.shell import ShellModuleConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from besdk import Module, Runtime

_SHUTDOWN_TIMEOUT_SECONDS = 30


@dataclass
class ModuleSpec:
    """描述外壳要装的一个模块。``be-ops`` 产出 4/7（合并清单 + 每外壳
    环境变量表）落地前，先用一个普通 dataclass 表达这个形状——判断与
    ``be-shell-go`` 的 ``ModuleSpec`` 一致，见
    ``docs/plans/04-阶段四-做外壳验拆回.md`` Task 2。
    """

    component_id: str
    component_version: str
    env: dict[str, str]
    http_port: int
    new_module: "Callable[[Runtime], Awaitable[Module]]"
    extra_ports: dict[str, int] = field(default_factory=dict)
    schema: str = ""


@dataclass
class Config:
    """外壳进程级的全部输入。字段含义与 ``be-shell-go`` 的 ``Config``
    逐一对应。
    """

    shell_name: str
    otel_base_url: str = ""
    pg_dsn: str = ""
    nats_url: str = ""
    iam_jwks_url: str = ""
    authz_bundle_url: str = ""
    health_port: int = 0
    modules: list[ModuleSpec] = field(default_factory=list)


@dataclass
class _Built:
    spec: ModuleSpec
    rt: "Runtime"
    mod: "Module"


def _env_with_process_fallback(specific: dict[str, str]) -> dict[str, str]:
    """把外壳自己的进程环境（``os.environ``）当一层兜底，叠加上
    ``specific``（这个模块自己的、be-ops shell-config 产出的
    config）——``specific`` 里已经有的 key 优先，兜底层只补
    ``specific`` 没提供的 key。同 ``be-shell-go`` 的
    ``envWithProcessFallback``，判断逐一对应。

    存在的理由：``genyaml.MergeConfig``（be-ops 侧）会把整个值是
    ``${VAR}`` 占位符的 configSchema 项（秘钥类：PEM 私钥、密码、
    webhook 共享密钥……）整条排除，不让它们进 ``SHELL_CONFIG_JSON``
    ——真机测过，这类值要么带着真实换行符会破坏 JSON 结构，要么展开
    后的真实密钥会被提交进 git（阶段四附加 Task 0.4）。这些秘钥因此
    改成外壳自己 component.yaml 上的独立 configSchema 项，brickKit
    原生的注入引擎会把它们展开成外壳容器**自己**的进程环境变量——
    本函数就是把这份"外壳自己才有、只有一个模块真正需要"的数据，
    兜底传给需要它的那个模块。本仓库目前唯一的模块（``infra-print``）
    没有这类秘钥，不会真的触发这条路径，但判断必须跟 ``be-shell-go``
    保持一致，真的有第二个 Python 组件加入 ``py-render`` 且带秘钥类
    配置项时，这里必须已经是对的。

    安全性同 ``be-shell-go`` 的既有判据：只对本项目已知只会被唯一一个
    模块使用的 key 有效，新增秘钥前先确认这条前提仍然成立。
    """
    out = dict(os.environ)
    out.update(specific)
    return out


async def run(cfg: Config, stop_event: asyncio.Event | None = None) -> None:
    """装配整个外壳：``bootstrap`` 一次 → 开一个共享连接池/NATS 连接 →
    ``init_shell_authz`` 一次 → 逐个模块调 ``new_module`` → 按同一顺序
    逐个跑迁移 → 起全部服务任务，`asyncio.wait(FIRST_COMPLETED)`` 等
    任一任务结束或 ``stop_event`` 被外部 set → 优雅关停全部任务。

    ``stop_event`` 留空时自己建一个——测试/独立验证骨架时用得上（外部
    调 ``stop_event.set()`` 触发优雅关闭，不需要真的发一个 OS 信号）；
    真实运行时由 ``main.py`` 传入一个绑定了 SIGTERM/SIGINT 处理器的
    ``Event``。
    """
    if stop_event is None:
        stop_event = asyncio.Event()

    logger = logging.getLogger(cfg.shell_name)

    shutdown_otel = await besdk.bootstrap(cfg.shell_name, cfg.otel_base_url)

    db = await asyncpg.create_pool(cfg.pg_dsn)
    nc = await nats.connect(cfg.nats_url)

    besdk.init_shell_authz(cfg.iam_jwks_url, cfg.authz_bundle_url, logger)

    built: list[_Built] = []
    for spec in cfg.modules:
        rt = besdk.new_shell_runtime(
            ShellModuleConfig(
                component_id=spec.component_id,
                component_version=spec.component_version,
                env=_env_with_process_fallback(spec.env),
                http_port=spec.http_port,
                extra_ports=spec.extra_ports,
            ),
            db,
            nc,
        )
        mod = await spec.new_module(rt)
        built.append(_Built(spec=spec, rt=rt, mod=mod))

    # 铁律五：合并后平台不再管迁移，外壳自己按 cfg.modules 给定的顺序跑，
    # 失败即中止启动（不是跳过这一个模块继续往下）。
    for b in built:
        if b.mod.migrations_dir is not None:
            _run_migrations(cfg.pg_dsn, b.spec.schema, b.mod.migrations_dir)

    tasks: list[asyncio.Task] = []
    if cfg.health_port:
        tasks.append(asyncio.create_task(_serve_health(cfg.health_port, stop_event)))
    for b in built:
        tasks.append(asyncio.create_task(besdk.serve_http(b.spec.http_port, b.mod.asgi_app, stop_event)))
        for name, port in b.spec.extra_ports.items():
            tasks.append(
                asyncio.create_task(besdk.serve_extra_port(name, port, b.mod.register_grpc, stop_event))
            )
        if b.mod.start is not None:
            tasks.append(asyncio.create_task(_watch_and_run_start(b, stop_event, logger)))

    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in done:
        exc = task.exception()
        if exc is not None:
            logger.error("服务异常退出：%s", exc)

    stop_event.set()
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.wait(pending)

    for b in built:
        if b.mod.stop is not None:
            await asyncio.wait_for(b.mod.stop(), timeout=_SHUTDOWN_TIMEOUT_SECONDS)

    await db.close()
    await nc.close()
    await shutdown_otel()


async def _watch_and_run_start(b: _Built, stop_event: asyncio.Event, logger: logging.Logger) -> None:
    """``Module.start`` 的既有约定是"取消时必须返回"（靠
    ``asyncio.Task.cancel()``，不是 ``stop_event``）——这里额外起一个
    watcher，``stop_event`` 被 set 时把 ``start()`` 这个协程本身取消掉，
    行为对齐 HTTP/gRPC 那两条走 ``stop_event`` 的路径，调用方不需要
    关心"这个模块的后台循环具体是哪种停止方式"。

    ⚠️ **同 be-shell-go Task 2 的教训**：单模块 ``Start()`` 里一次未
    捕获的异常，在这里必须被这一层 ``try/except`` 接住转成日志——不然
    会直接从 ``asyncio.wait`` 的 ``done`` 集合里冒出来，行为上虽然不会
    像 Go 的裸 panic 那样直接杀死整个进程（Python 的异常传播机制没有
    这个问题），但缺了 ``module_component_id`` 这个上下文，排障时不知道
    是哪个模块的后台循环坏的。
    """
    watcher = asyncio.create_task(stop_event.wait())
    task = asyncio.create_task(b.mod.start())
    try:
        done, _ = await asyncio.wait({watcher, task}, return_when=asyncio.FIRST_COMPLETED)
        if task in done:
            exc = task.exception()
            if exc is not None:
                logger.error(
                    "模块后台循环退出", extra={"module_component_id": b.spec.component_id}, exc_info=exc
                )
                raise exc
        else:
            task.cancel()
    finally:
        watcher.cancel()


async def _serve_health(port: int, stop_event: asyncio.Event) -> None:
    """外壳自己（不是任何一个模块）对外暴露的健康检查端口——设计书
    §13.6："健康检查……一个容器只有一个 probe"，但外壳里的 N 个模块各有
    各的端口和各自的 ``/healthz``，谁的健康检查代表整个容器，平台不知道
    也不会替这个新问题给出答案。这里只答"外壳进程本身活着"，不查任何
    模块、不查任何依赖，同每个模块自己 ``/healthz`` 的既有判据。

    ⚠️ **真机踩到的坑（阶段四附加 Task 0.4，servedBy 真机验证）**：这里
    必须同 ``besdk.fastapi_app`` 里 ``/healthz`` 的既有判据一样，路径
    写 ``/healthz``（不是 ``/``，要跟 ``component.yaml`` 的
    ``healthCheck.path`` 对上）、且显式同时注册 ``@app.get``/``@app.head``
    两个方法——Starlette 的 ``Route.__init__`` 虽然会给 ``GET`` 自动带上
    ``HEAD``，但 ``FastAPI.get()`` 走的是自己单独的路径，不会带过来这条
    自动加线，``brickkit`` 生成的健康检查命令用 ``wget --spider``（发的
    是 HEAD），只注册 GET 会让每一次探测都 404/405，直接见
    ``besdk/fastapi_app.py`` 顶部同一个坑的完整记录。
    """
    from fastapi import FastAPI

    app = FastAPI()

    @app.get("/healthz")
    @app.head("/healthz")
    async def _healthz() -> dict[str, bool]:
        return {"ok": True}

    await besdk.serve_http(port, app, stop_event)


def _run_migrations(shared_dsn: str, schema: str, migrations_dir: Path) -> None:
    """对一个模块的迁移目录跑一次 apply——DSN 沿用外壳共享的连接串
    （``besdk.build_pg_dsn()`` 给的是 ``postgres://`` scheme，yoyo 要
    ``postgresql://``），只按这个模块的 schema 追加
    ``options=-csearch_path=<schema>``（同 ``infra-print`` 自己
    ``backend/app/migrate.py`` 的既有约定）。
    """
    dsn = shared_dsn.replace("postgres://", "postgresql://", 1)
    options = quote(f"-csearch_path={schema}")
    sep = "&" if "?" in dsn else "?"
    dsn = f"{dsn}{sep}options={options}"

    backend = get_backend(dsn)
    migrations = read_migrations(str(migrations_dir))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))
