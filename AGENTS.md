# be-shell-python · AI 助手导读

## 身份证

| 项 | 值 |
|---|---|
| 仓库名 | `be-shell-python` |
| 目录 | `shells/python/`（不进 `brickkit.yaml`，不是 brickKit 组件） |
| 语言 / 框架 | Python 3.12；只依赖 `besdk`（be-sdk-python）、`yoyo-migrations`、`asyncpg`、`nats-py` |
| 装的模块 | 阶段四：1 个 Python 组件（`infra-print`）——现状（Task 3 骨架）还没有真实装进，见 `README.md` 待办 |
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
  不要凭直觉假设现在这份实现直接够用。
- `Config` 的连接串/权限判定地址/每个 `ModuleSpec.env` 目前只能靠 `main.py`
  手写或读裸环境变量——`be-ops` 产出 4/7 落地后要换成读生成产物，不是继续手写。
- `health_port` 目前默认写死——`be-ops` 产出 8（`shell-compose.yml`）落地后由
  那一步决定这个端口该是多少。

## 测试

`shell/run.py` 的测试全部用假模块（`tests/test_run.py`），不依赖任何真实组件
仓库——外壳骨架测的是"装配机制本身对不对"，不是"某个具体组件对不对"。需要真实
可达的 `TEST_PG_DSN`（同 `besdk` 既有判据未设置就跳过）——`asyncpg.create_pool`
默认会真的建立连接，不是懒加载；`TEST_NATS_URL` 可选，默认
`nats://127.0.0.1:4222`。
