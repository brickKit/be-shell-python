# be-shell-python

Python 外壳的启动器：把 N 个组件模块（各自的 `Module`）装进同一个进程，共享一个
`asyncpg.Pool` + 一个 NATS 连接 + 一套 OTel/权限判定，各自 serve 自己的 HTTP/gRPC
端口。设计动机、七条铁律、代价见父仓库《BrickEnterprise 设计书.md》第 13 章；
本仓库只负责"怎么实现"，不重复"为什么这样做"。Go 版对应仓库是 `be-shell-go`，
两者的判断逻辑逐一对应（同一份阶段四计划文档 Task 2/3）。

**它不是 brickKit 组件**——不进 `brickkit.yaml`，不受签名覆盖。

## 现状（阶段四 Task 7 完成，`infra-print` 真机原子式切换）

- `shell/run.py`：全部装配逻辑（`run`），复用 `besdk`（be-sdk-python）已验证过的
  `new_shell_runtime`/`init_shell_authz`/`serve_http`/`serve_extra_port`，不重新
  实现。编排方式沿用 `run_standalone` 自己的既有习惯（`asyncio.wait(...,
  FIRST_COMPLETED)` + 手动 `stop_event.set()` + 对 pending 任务 `.cancel()`），
  不是 `asyncio.TaskGroup`——`serve_http`/`serve_extra_port` 的既有签名是"传一个
  `stop_event`，等它被 set 之后自己优雅退出"，`Module.start` 的既有约定则是
  "取消时必须返回"，两种停止方式混着用，`run_standalone` 早就用前者的编排方式
  解决了，外壳只是把"1 个模块的任务集合"换成"N 个模块的任务集合拼在一起"。
- `main.py`：进程入口，已经接上 `be-ops` 产出 4（`SHELL_CONFIG_JSON`）/产出 7
  （`SHELL_ENV_JSON`）——按 `SHELL_NAME` 从两份数据文件里挑出自己要装的外壳，
  `_MODULE_REGISTRY` 是本仓库唯一"componentId 字符串 → 真实 Python 源码 import"
  的静态映射（同 `be-shell-go` 的 `moduleRegistry`，判断逐一对应）。本仓库目前
  只对应 1 个外壳实例（`py-render`，唯一成员 `infra/print`）。
- 已用假模块验证过骨架本身（`tests/test_run.py`）：多模块共享 db/nats 但各自
  独立字段、外壳自己的 `health_port` 独立于任何模块响应、单模块 `start()` 里的
  异常被干净地记录下来（带 `module_component_id`）且不会让整个进程崩溃、
  `stop_event` 被 set 后全部优雅退出。
- **真的把 `infra-print`（全系统第一个 Python 组件，未经任何修改）装进
  `shell.run`**（`tests/test_real_module.py`）：真实迁移在 `brickkit_test_db`
  的 `infra_print` schema 里跑通、健康检查真实响应、`/infra/print/templates`
  真实 fail-closed（未配 `iamJwksUrl` 时 403，配了但没带 token 时 401——两条路径
  都真机验证过，见下）。
- **真机部署验证（阶段四 Task 7 最后一步，2026-09-13 完成）**：`docker build`
  出的镜像真的起了 `shell-py-render` 一个容器，真实迁移在 `brickkit_db` 里跑通
  （`shell_py_render` 登录角色，`make db-init` 早就建好、真机验证过能登录）、
  健康检查 200、`/infra/print/templates` 在配了 `iamJwksUrl` 但未带 token 时
  返回 401（同 be-shell-go 的既有行为）。`brickkit.yaml` 也已真机原子式切换成
  `local: true`——`brickkit up` 之后只剩 `frontend-standard`/`infra-bff-mobile`
  两个独立容器，`infra-print` 不再生成自己的容器。

## 踩到的真实坑

- **`infra-print` 自己的 `besdk` 依赖（v0.3.1）与本仓库（v0.3.3）冲突**：把
  `infra-print` 直接装成 pip 依赖时，`uv`/`pip` 的 git URL 依赖解析不接受同一个
  包出现两个不同 tag，直接报错拒绝安装。核对过 v0.3.1..v0.3.3 只新增了
  `besdk/shell.py` 一个外壳级构件模块，`infra-print` 用到的既有 API 一字未动，
  于是把 `infra-print` 自己的依赖升到 v0.3.3（`infra-print@v1.0.5`，27 个既有
  测试全部保持绿），而不是反过来把本仓库降级去将就一个更早的 SDK 版本。
- **`Module.migrations_dir` 是相对 CWD 的 `Path`，不是 Go 版那种编译进二进制的
  `fs.FS`，`infra-print` 的 `migrations/` 目录也不是它自己 Python 包的一部分**
  （`pyproject.toml` 的 `[tool.setuptools.packages.find]` 只收 `app*`/`infra*`）
  ——`pip install` 装不到这份目录。`Dockerfile` 因此在 build 阶段单独
  `git clone` 一次对应 tag、只为拿 `migrations/`（`ARG INFRA_PRINT_VERSION`
  必须跟 `pyproject.toml` 里的依赖 tag 手动保持一致，两处都要改）；本地测试
  （`tests/test_real_module.py`）同样现 clone 一份到临时目录再 `chdir` 过去，
  不依赖"本机恰好在同一个 monorepo 里检出了 `components/infra/print`"这个
  环境假设。真的有第二个 Python 组件加入 `py-render` 时，"migrations 目录直接
  放 CWD 根"这个做法会撞车，先读 `shell/run.py` 顶部的完整说明。

完整任务清单见父仓库 `docs/plans/04-阶段四-做外壳验拆回.md`。
