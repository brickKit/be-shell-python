# 多阶段构建——同 infra-print 的既有模式：besdk 是 git+https 依赖，
# pip install 需要 git 二进制去 clone，git/build-essential 只留在 build
# 阶段，不进最终镜像。
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY shell ./shell
COPY main.py ./

RUN pip install --no-cache-dir --prefix=/install .

# ⚠️ 阶段四 Task 7：infra-print 的 migrations/ 目录不是它自己 Python 包
# 的一部分（`pyproject.toml` 的 `[tool.setuptools.packages.find]` 只收
# `app*`/`infra*`，不收 `migrations/`——同它自己 Dockerfile 用独立
# `COPY migrations ./migrations` 步骤的既有做法），`pip install` 装不到
# 这份目录，必须单独 clone 一次。**这里的 tag 必须跟上面 pyproject.toml
# 里 infra-print 的 git+https 依赖版本保持一致**——两处都改，不能只改
# 一处（同本项目"依赖版本号同步"这类坑的既有教训，见根
# docs/dev/field-tested-pitfalls-log.md C16）。
ARG INFRA_PRINT_VERSION=v1.0.5
RUN git clone --depth 1 --branch ${INFRA_PRINT_VERSION} \
    https://github.com/brickKit/infra-print.git /tmp/infra-print

FROM python:3.12-slim

# 真机验证过的 WeasyPrint 系统依赖（抄自 infra-print 自己的 Dockerfile，
# 见它自己 AGENTS.md 的坑清单："只装 libpango/libcairo/libgdk-pixbuf
# 不装字体，中文 PDF 会退化成乱码"）：infra-print 现在合并进本外壳里
# 跑，同一套系统依赖必须原样搬过来，不能假设"pip 装完 Python 包就够了"。
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 libgdk-pixbuf-2.0-0 libcairo2 \
    fontconfig fonts-noto-cjk \
    wget ca-certificates tzdata \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

# 基底必须带 shell（wget），brickKit 的健康检查是 CMD-SHELL + wget
# ——这条对外壳同样成立，虽然外壳不是 brickKit 组件，健康检查是
# be-ops 产出 8 自己配的，但探测方式沿用同一约定。

COPY --from=build /install /usr/local

WORKDIR /app
COPY main.py ./
COPY shell ./shell
# infra-print 的 Module.migrations_dir 是相对 CWD 的 Path("migrations")
# （见 shell/run.py 顶部说明的已知缺口）——CWD 就是这里的 WORKDIR /app，
# 跟 infra-print 自己单跑时的 CWD 约定一致，不需要额外传路径。
COPY --from=build /tmp/infra-print/migrations ./migrations

# ⚠️ 真的有第二个 Python 组件加入 py-render 时（阶段五/六），这里的
# "migrations 目录直接放 CWD 根"这个做法会撞车——两个模块的
# Path("migrations") 会解析到同一个目录。先读 shell/run.py 顶部的完整
# 说明，不要凭直觉往下加一行 COPY 了事。

ENTRYPOINT ["python", "-m", "main"]
