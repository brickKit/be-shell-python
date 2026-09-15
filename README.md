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
- `main.py`：进程入口，`_build_modules` 直接解析平台原生注入的
  `BRICKKIT_SERVED_MEMBERS_CONFIG`（brickKit v0.4.2 起原生支持，见下方
  "现状补充"：一个 JSON 数组，元素就是这次真的被这个外壳收编、活着的成员，
  含 `componentId`/`version`/`httpPort`/`extraPorts`/合并后的 `config`），不再
  需要单独去交叉核对 `BRICKKIT_SERVED_MEMBERS`——数组本身天然只包含收编成员。
  `config` 的键是原始 configSchema 驼峰 key，`_config_env_var_name` 转成
  `SCREAMING_SNAKE_CASE` 后才是 `besdk.Config` 能查到的名字（算法与 brickKit
  自己的 `EnvVarName`、`be-shell-go` 的 `configEnvVarName` 逐字对应）。
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

## 现状补充（阶段四 Task 9 完成，2026-09-13）

`shell/run.py` 的 `run()` 新增 `_export_dependency_endpoints`：把每个模块 `env` 里 `_ENDPOINT`
结尾的 key 真的写进 `os.environ`——同 `be-shell-go` 阶段四 Task 9 真机撞到的同一个 bug
（`besdk.endpoint()` 读的是 `os.environ`，不是 `rt.config`，两边实现逐字对应）。本仓库目前唯一的
模块（`infra-print`）没有任何依赖边，不会真的触发这条路径，但判断必须跟 `be-shell-go` 保持一致。
完整根因分析见 `be-shell-go` 的 README 或父仓库 `docs/plans/04-阶段四-做外壳验拆回.md` Task 9。

⚠️ **这段代码本身已在阶段四附加 Task 0.2/0.3 里退休**，见下方对应"现状补充"——根因分析依然成立，
只是"谁负责把值放进 `os.environ`"这件事的责任方从外壳自己换成了平台。

## 现状补充（阶段四附加 Task 0.2/0.3 完成，2026-09-14）——servedBy 落地，上面 Task 9 的修复代码退休了

brickKit 新增了 `servedBy` 机制之后，上面 Task 9 那条 `_export_dependency_endpoints`——连同它要读的
`be-ops` 产出 7（`SHELL_ENV_JSON`）——整个退休了：`servedBy` 落地后，brickKit 自己在生成阶段就把
`*_ENDPOINT` 类变量直接合并进外壳容器**自己的** `os.environ`，这个进程一启动就已经看得见，不再需要
`shell/run.py` 自己再写一遍。函数本体与它的两条回归测试（`test_export_dependency_endpoints_*`）已从
`shell/run.py`/`tests/test_run.py` 删除。

`main.py` 的 `_build_modules` 同时换了第二件事：不再无条件把 `SHELL_CONFIG_JSON` 里列出的模块全部
实例化，改成额外按平台原生注入的 `BRICKKIT_SERVED_MEMBERS`（这次真的被收编、活着的成员，逗号分隔的
版本化服务名）筛一遍，判断逐一对应 `be-shell-go` 的 `buildModules`。每个模块自己的 `configSchema`
解析结果（原来 `SHELL_ENV_JSON` 的 `env` 字段）也一并挪进了 `SHELL_CONFIG_JSON` 新增的 `config` 字段
（`be-ops` 侧的完整调研过程见装配仓库 `docs/plans/04b-验证记录.md` Task 0.2）。

## 现状补充（阶段四附加 Task 0.4 完成，2026-09-15）——SHELL_CONFIG_JSON 从"文件路径"改成"内容本身"

判断逐一对应 `be-shell-go` 同一次改动：真机把 `brickkit.yaml` 全量切到真实 `servedBy` 后，外壳容器
crash-loop（`SHELL_CONFIG_JSON` 未设置）——根因是 brickKit 的 manifest 模型没有 `volumes` 字段，
"文件路径 + 挂载卷"这条路走不通。改成环境变量的值直接是 `be-ops shell-config --shell py-render`
打印出的、这一个外壳自己的 `modules` 数组（compact JSON），跟 `infra/authz` 的 `permissionCatalog`
是同一种模式，写死在 `brickkit.yaml` 的 `config.shellConfigJson` 里。`_build_modules` 因此不再接收
`shell_name` 参数——内容从生成的那一刻起就已经只属于这一个外壳。完整过程与真机复核结果见装配仓库
`docs/plans/04b-验证记录.md` Task 0.4。

## 现状补充（阶段四 Task 8 完成，2026-09-13）

Task 7 验证阶段那个手动 `docker run` 起的容器，已经换成真正的
`infra/shell-compose.yml`（父仓库根目录）——接上了健康检查、`be-net`、
`SHELL_CONFIG_JSON`/`SHELL_ENV_JSON` 的自动生成与挂载（父仓库
`make shell-gen`/`make shell-up`）。⚠️ **真机撞到的坑**：健康检查最初写的
是 `wget --spider`（HEAD 请求），FastAPI 的 `@app.get("/")` 默认不支持
HEAD，返回 405 被 `wget` 判定成"链接不存在"（退出码 8），容器因此被
Docker 判定成 unhealthy——即使进程本身完全正常、`GET` 请求真的能拿到
`{"ok":true}`。Go 侧的 `internal/shell` 健康检查是裸 `http.HandlerFunc`
（不区分方法），一直没暴露这个问题，只有 Python 侧的 FastAPI 路由才踩到。
改成普通 `GET`（`wget -q -O /dev/null`）后两边行为一致。完整细节见父仓库
`docs/plans/04-阶段四-做外壳验拆回.md` Task 8。

完整任务清单见父仓库 `docs/plans/04-阶段四-做外壳验拆回.md`。

## 现状补充（阶段四附加 Task 0.6 完成，2026-09-15）——`SHELL_CONFIG_JSON` + `be-ops shell-config` 整体退休

判断与理由跟 `be-shell-go` 同一次改动完全对应（见其 README 同名一节的完整叙事：
两次真机复发的"手工维护数据过期"失败模式 + 验证组件独立启动能力的摩擦点，两份
架构提案已被 brickKit v0.4.2 完整采纳，新增 `BRICKKIT_SERVED_MEMBERS_CONFIG` +
`brickkit up --ignore-served-by`）。本仓库这边的改动：

- `main.py` 的 `_build_modules` 改成直接解析 `BRICKKIT_SERVED_MEMBERS_CONFIG`
  （不再需要单独按 `BRICKKIT_SERVED_MEMBERS` 筛一遍——这份新变量本身就已经是
  "这次真的收编了谁"），补一个 `_config_env_var_name`（用
  `(?<=[a-z0-9])(?=[A-Z])` 正则找驼峰边界插下划线再转大写）把原始驼峰 key 转成
  `SCREAMING_SNAKE_CASE`，跟 brickKit 自己的 `EnvVarName`、`be-shell-go` 的
  `configEnvVarName` 逐字对应（已用同一批真实 configSchema key 名对三份实现
  逐一核对过，包括 `appTokenSigningKeyPem`、`accessTokenTtlSeconds` 这类边界
  情况）。
- `shell/run.py` 的 `_env_with_process_fallback`（此前给密钥类配置项兜底用的
  "外壳自己进程环境当兜底层"）整个删除——`BRICKKIT_SERVED_MEMBERS_CONFIG` 由
  brickKit 自己的 Go `encoding/json` 序列化生成，正确处理带原始换行符的密钥值
  （不会撑坏 JSON），本仓库目前唯一模块（`infra/print`）本来就没有密钥类配置项，
  但判断需要跟 `be-shell-go` 保持一致。
- `deploy/shell/py-render/component.yaml` 删掉了 `shellConfigJson` 这个
  configSchema 项——`be-ops shell-config` 子命令、`SHELL_CONFIG_JSON` 手工维护
  这条线已经没有存在的理由。

真机复核结果见父仓库 `docs/plans/04b-验证记录.md` Task 0.6。

## 现状补充（阶段四附加 Task 0.6 修补，2026-09-15）——`_env_with_process_fallback` 恢复

上面一节"整个删除 `_env_with_process_fallback`"的判断，`be-shell-go` 那边真机
`brickkit up` 复现出是错的——根因跟一开始的直觉相反：`BRICKKIT_SERVED_MEMBERS_
CONFIG` 这个 JSON 在 brickKit 生成它的那一刻确实是合法的（密钥类的值在
`brickkit.yaml` 里还是 `${VAR}` 占位符，没有特殊字符）。**真正的展开发生在
docker compose 自己读取生成好的 docker-compose.yaml 时**——它对整份文件按纯
文本做 `${VAR}` 替换，不知道某个 `${VAR}` 恰好嵌在一段本该是合法 JSON 的字符串
内部，真实密钥的原始换行符替换进去会直接把 JSON 断开（`be-shell-go` 的
`shell-go-infra` 因此 crash-loop，完整根因分析见其 README 同名一节）。这是
`BRICKKIT_SERVED_MEMBERS_CONFIG` 机制本身的普适性设计缺口，已反馈给 brickKit。

`shell/run.py` 的 `_env_with_process_fallback` 与它的回归测试原样恢复，判断
重新跟 `be-shell-go` 保持一致。本仓库目前唯一模块（`infra/print`）没有密钥类
配置项，这个 bug 不会真的在本仓库触发，恢复纯粹是为了保持两个外壳仓库的判断
逐一对应——真的有第二个 Python 组件带密钥类配置项加入 `py-render` 时，这里
必须已经是对的。真机复核结果见父仓库 `docs/plans/04b-验证记录.md` Task 0.6。
