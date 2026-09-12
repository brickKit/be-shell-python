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

FROM python:3.12-slim

# 基底必须带 shell（wget），brickKit 的健康检查是 CMD-SHELL + wget
# ——这条对外壳同样成立，虽然外壳不是 brickKit 组件，健康检查是
# be-ops 产出 8 自己配的，但探测方式沿用同一约定。
RUN apt-get update && apt-get install -y --no-install-recommends wget ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /install /usr/local

WORKDIR /app
COPY main.py ./
COPY shell ./shell

# ⚠️ Task 5/6-等价任务（把真实 Python 组件正式接进来）落地后，这里要
# 补两件事：① 每个被装进来的 Python 组件自己需要的系统依赖（例如
# infra-print 的 WeasyPrint 字体包）都要加进本阶段；② COPY 对应组件的
# migrations/ 目录进来，供 shell.run._run_migrations 在真实 CWD 下解析
# 相对路径（见 shell/run.py 顶部说明的已知缺口）。

ENTRYPOINT ["python", "-m", "main"]
