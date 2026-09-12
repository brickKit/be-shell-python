# be-shell-python 既不是 brickKit 组件也不是纯横切库——按总纲 §I 的 9
# 个门禁目标写，保持与其它仓库一致的心智模型；对本仓库现状没意义的项
# 如实标 N/A，不硬凑。
.DEFAULT_GOAL := help
.PHONY: help check-version test image migrate-idempotent dag-check contract-check \
        import-scan smoke module-check all

help:  ## 列出所有目标
	@awk 'BEGIN{FS=":.*##"; printf "\n用法: make <目标>\n\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2} \
	     /^##@/ {printf "\n\033[1m%s\033[0m\n", substr($$0,5)}' $(MAKEFILE_LIST)
	@echo ""

##@ 对本仓库现状没意义的（阶段四真的装进真实模块之前）
check-version:  ## N/A：不是 brickKit 组件，没有 component.yaml
	@echo "N/A：非组件仓库，没有 component.yaml"

migrate-idempotent:  ## N/A：迁移由 shell.run.run 按模块跑，不是独立的迁移命令
	@echo "N/A：迁移逻辑在 shell/run.py 里随 run 一起测，没有独立的迁移命令"

contract-check:  ## N/A：没有 contracts/，外壳不持有任何契约
	@echo "N/A：外壳只组装模块，不定义任何契约"

smoke:  ## N/A：需要真实模块 + shell-compose 才有意义，真的装进模块之前不适用
	@echo "N/A：真的装进真实模块、接上 shell-compose 之前，没有可冒烟的对象"

module-check:  ## N/A：外壳是 Module 的消费方，不是提供方
	@echo "N/A：这条门禁检查组件自己的 module 是否合规，外壳不提供 module"

##@ 真实生效的
test:  ## pytest -v（需要真实可达的 TEST_PG_DSN + 可选 TEST_NATS_URL，同 be-sdk-python 既有判据）
	.venv/bin/python -m pytest -v

image:  ## 构建外壳镜像（本地构建，不在 brickKit 签名覆盖范围内）
	docker build -t brickenterprise/be-shell-python:$${VERSION:-dev} .

dag-check:  ## 包依赖图无环（Python 没有编译期强制，用 import 探测循环 import）
	@.venv/bin/python -c "import shell" && echo "✓ shell 包本身无循环 import"

import-scan:  ## 铁律六：外壳可以依赖各组件的公开包，但组件之间绝不能互相依赖——本仓库自己不会出现这条边，真正的守卫在根 make gates（阶段四后续任务扩展范围）
	@echo "✓ 本仓库自身没有能力违反铁律六——真正的守卫在根 make gates 的扫描器"

all: test dag-check import-scan  ## 本仓库当前有意义的门禁全部跑一遍
