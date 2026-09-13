# be-shell-python · AI 助手导读

## 身份证

| 项 | 值 |
|---|---|
| 仓库名 | `be-shell-python` |
| 目录 | `shells/python/`（不进 `brickkit.yaml`，不是 brickKit 组件） |
| 语言 / 框架 | Python 3.12；只依赖 `besdk`（be-sdk-python）、`yoyo-migrations`、`asyncpg`、`nats-py` |
| 装的模块 | 阶段四：1 个 Python 组件（`infra-print`）——已经真机装进、迁移/健康检查/路由全部验证过，`brickkit.yaml` 也已真机原子式切换成 `local: true`，见 `README.md` |
| 设计真相源 | 《BrickEnterprise 设计书.md》第 13 章（为什么、七条铁律、代价）+ `docs/plans/04-阶段四-做外壳验拆回.md`（本仓库具体要做什么）+ `docs/design/_调研记录/04-阶段四.md`（技术判断的推演过程）+ `shells/go/AGENTS.md`（Go 版对应仓库，判断逻辑逐一对应）——本文件与它们冲突时，以那些为准 |

## 这个仓库存在的唯一理由

把 N 个组件模块（各自的 `Module`）塞进一个进程——除此之外不做任何事。**外壳不许
做的事**（`shells/README.md`、设计书 §13.3 铁律六）：

- ❌ 不许让两个组件模块直接互相 `import`
- ❌ 不许把两个组件的表放进同一个 schema、不许跨 schema JOIN
- ❌ 不许把 N 个模块的 API 合并成一个端口（`ModuleSpec.http_port` 必须逐个不同）
- ❌ 不许在外壳里写任何业务逻辑

## 核心判断：复用 `besdk`，不重新实现

`shell/run.py` 的字段构造、serve/优雅关闭，全部调用 `besdk`（be-sdk-python）已经
在 `run_standalone`（单模块场景）里验证过的
`new_shell_runtime`/`init_shell_authz`/`serve_http`/`serve_extra_port`——**新增
任何"外壳要多做一点什么"的需求，第一反应应该是"这段逻辑要不要提到 `besdk` 里、
变成一个可以被 `run_standalone` 和外壳共同复用的构件"，而不是直接在这个仓库里
另起一份实现**。理由是阶段三踩坑记录 A4g：一条只有小范围调用方走过的构造路径，
跟生产真正走的那条路径不是同一条，就是真实 bug 藏身的地方。

**编排方式沿用 `run_standalone` 自己的既有习惯**（`asyncio.wait(...,
FIRST_COMPLETED)` + 手动 `stop_event.set()` + 对 pending 任务 `.cancel()`），
不是更"时髦"的 `asyncio.TaskGroup`——`serve_http`/`serve_extra_port` 的既有签名
依赖一个外部传入的 `stop_event`，`Module.start` 的既有约定是"取消时必须返回"，
两种停止方式已经在 `run_standalone` 里被这套编排方式解决过，外壳不应该为了引入
一个新概念（`TaskGroup`）而在同一个 SDK 生态里制造第二套不完全一致的停止语义。

## 已知的、故意留到后面任务的缺口（不是遗漏，是任务顺序）

- `Module.migrations_dir` 是相对 CWD 的 `Path`，不是 Go 版那种编译进二进制的
  `fs.FS`——本阶段只装 1 个 Python 模块，不会撞见"两个模块的相对路径解析到同一个
  CWD"这个问题；真的有第二个 Python 组件时，先读 `shell/run.py` 顶部的完整说明，
  不要凭直觉假设现在这份实现直接够用。`infra-print` 的 `migrations/` 目录本身
  也不是它 Python 包的一部分，`Dockerfile`/测试都靠单独 `git clone` 一次对应
  tag 来拿这份目录，见 `README.md`"踩到的真实坑"一节。
- `main.py` 已经接上 `be-ops` 产出 4/7（`SHELL_CONFIG_JSON`/`SHELL_ENV_JSON`
  两个环境变量指向的 JSON 文件），不再手写 `Config`/`ModuleSpec`——现在由父
  仓库根目录的 `infra/shell-compose.yml` 挂载（`make shell-up` 会先
  `make shell-gen` 重新生成两份 JSON），Task 8 已完成，不再是 Task 6/7 阶段
  手动 `docker run -v` 的临时状态。
- ⚠️ **真机踩到的坑**：健康检查命令最初写的是 `wget --spider`（HEAD 请求），
  FastAPI 的 `@app.get("/")` 默认不支持 HEAD，返回 405 被 `wget` 判定成链接
  不存在（退出码 8），容器因此被 Docker 判定成 unhealthy——即使进程完全正常、
  `GET` 请求真的能拿到 `{"ok":true}`。改成普通 `GET`（`wget -q -O /dev/null`）
  解决，见 `infra/shell-compose.yml`。Go 侧的健康检查是裸 `http.HandlerFunc`
  （不区分方法），没有这个问题，但两边判据要保持一致。

## 测试

`shell/run.py` 的骨架测试用假模块（`tests/test_run.py`），不依赖任何真实组件
仓库——外壳骨架测的是"装配机制本身对不对"，不是"某个具体组件对不对"。需要真实
可达的 `TEST_PG_DSN`（同 `besdk` 既有判据未设置就跳过）——`asyncpg.create_pool`
默认会真的建立连接，不是懒加载；`TEST_NATS_URL` 可选，默认
`nats://127.0.0.1:4222`。

`tests/test_real_module.py` 额外真的装了 `infra-print`（真实、未经任何修改的
组件模块），需要同一个 `TEST_PG_DSN`/`TEST_NATS_URL`，外加真实网络访问
（`git clone` 对应 tag 拿 `migrations/` 目录，见 `README.md`"踩到的真实坑"
一节）——本仓库的依赖本来就是 git+https 形式，运行测试环境已经假设了这条网络
访问路径存在，不是新增的前提。`tests/test_main.py` 测 `main._build_modules`
这一层装配逻辑本身（按外壳挑模块、外壳不存在/未切换时报错），不需要真实
基础设施，同 `be-shell-go` 的 `cmd/shell/main_test.go`。
