# be-shell-python

Python 外壳的启动器：把 N 个组件模块（各自的 `Module`）装进同一个进程，共享一个
`asyncpg.Pool` + 一个 NATS 连接 + 一套 OTel/权限判定，各自 serve 自己的 HTTP/gRPC
端口。设计动机、七条铁律、代价见父仓库《BrickEnterprise 设计书.md》第 13 章；
本仓库只负责"怎么实现"，不重复"为什么这样做"。Go 版对应仓库是 `be-shell-go`，
两者的判断逻辑逐一对应（同一份阶段四计划文档 Task 2/3）。

**它不是 brickKit 组件**——不进 `brickkit.yaml`，不受签名覆盖。

## 现状（阶段四 Task 3，骨架）

- `shell/run.py`：全部装配逻辑（`run`），复用 `besdk`（be-sdk-python）已验证过的
  `new_shell_runtime`/`init_shell_authz`/`serve_http`/`serve_extra_port`，不重新
  实现。编排方式沿用 `run_standalone` 自己的既有习惯（`asyncio.wait(...,
  FIRST_COMPLETED)` + 手动 `stop_event.set()` + 对 pending 任务 `.cancel()`），
  不是 `asyncio.TaskGroup`——`serve_http`/`serve_extra_port` 的既有签名是"传一个
  `stop_event`，等它被 set 之后自己优雅退出"，`Module.start` 的既有约定则是
  "取消时必须返回"，两种停止方式混着用，`run_standalone` 早就用前者的编排方式
  解决了，外壳只是把"1 个模块的任务集合"换成"N 个模块的任务集合拼在一起"。
- `main.py`：进程入口，目前 `modules` 是空的——真实的 `infra-print`（本阶段唯一
  要装的 Python 组件）要等它正式作为依赖被 `pyproject.toml` 声明之后才会填。
- 已用假模块验证过骨架本身：多模块共享 db/nats 但各自独立字段、外壳自己的
  `health_port` 独立于任何模块响应、单模块 `start()` 里的异常被干净地记录下来
  （带 `module_component_id`）且不会让整个进程崩溃、`stop_event` 被 set 后全部
  优雅退出（细节见 `tests/test_run.py`）。

## 已知的、故意留到后面的缺口

- **`Module.migrations_dir` 是相对 CWD 的 `Path`，不是 Go 版那种编译进二进制的
  `fs.FS`**——本阶段外壳只装 1 个 Python 模块（`infra-print`），不会撞见"两个
  模块的相对路径解析到同一个 CWD"这个问题；阶段五/六真的有第二个 Python 组件时，
  这里需要先解决"每个模块的 `migrations_dir` 到底相对什么路径解析"，不能假设
  现在这份实现直接够用（`shell/run.py` 顶部有完整说明）。
- `Config.pg_dsn`/`nats_url`/`iam_jwks_url`/`authz_bundle_url`/每个
  `ModuleSpec.env` 目前只能靠 `main.py` 手写或读裸环境变量——`be-ops` 产出 4/7
  落地后，这里要换成读它们生成的合并清单/环境变量表文件。
- `health_port` 目前默认写死 `18889`——`be-ops` 产出 8（`shell-compose.yml`）
  落地后，这个端口该是多少、健康检查探针的具体命令，由那一步决定。

完整任务清单见父仓库 `docs/plans/04-阶段四-做外壳验拆回.md`。
