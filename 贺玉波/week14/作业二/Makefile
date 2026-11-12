SHELL := /bin/bash
ROOT := /workspace/week14
BACKEND := $(ROOT)/backend
PY := /usr/local/miniconda3/envs/py312/bin/python
PIP := /usr/local/miniconda3/envs/py312/bin/pip
CTL := /usr/local/miniconda3/envs/py312/bin/supervisorctl
CONF := $(ROOT)/deploy/supervisord.conf

.DEFAULT_GOAL := help

.PHONY: help bootstrap up up-core down restart status logs db-init topics smoke test test-unit test-int test-model reindex eval frontend-install frontend-build fmt lint api-schema demo-redis demo-kafka clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

bootstrap: ## 安装依赖并校验配置
	bash deploy/scripts/bootstrap.sh

up: ## 启动全部进程并等待 /ready 全绿
	bash deploy/scripts/up.sh all

up-core: ## 只起中间件 + api（不加载 vLLM，快速调试）
	bash deploy/scripts/up.sh core

down: ## 停止全部受管进程
	bash deploy/scripts/down.sh

restart: ## 重启 api
	$(CTL) -c $(CONF) restart api

status: ## 查看进程状态
	$(CTL) -c $(CONF) status

logs: ## 实时跟踪 api 与 worker 日志
	tail -f $(ROOT)/logs/api.log $(ROOT)/logs/worker.log

db-init: ## 建库并执行 sql/*.sql
	bash deploy/scripts/migrate.sh

topics: ## 幂等创建 Kafka topic
	bash deploy/scripts/create_topics.sh

smoke: ## 探针 + 关键接口冒烟
	bash scripts/smoke.sh

demo-redis: ## 作业1-A：Redis 会话/答案缓存演示
	cd $(ROOT) && PYTHONPATH=$(BACKEND) $(PY) demo/redis_chat.py

demo-kafka: ## 作业1-B：Kafka 生产-消费-重试-DLQ 演示
	cd $(ROOT) && PYTHONPATH=$(BACKEND) $(PY) demo/kafka_pipe.py

test: ## 单测 + 集成测试（真实中间件）
	cd $(BACKEND) && $(PY) -m pytest

test-unit: ## 只跑单测
	cd $(BACKEND) && $(PY) -m pytest -m "not integration and not gpu"

test-int: ## 只跑集成测试
	cd $(BACKEND) && $(PY) -m pytest -m integration

test-model: ## 真实向量模型冒烟（首次会下载权重，需 GPU）
	cd $(BACKEND) && $(PY) -m pytest -m gpu -s

reindex: ## 重建全部文档的向量（清空后按已解析产物重跑）
	cd $(ROOT) && PYTHONPATH=$(BACKEND) $(PY) -m app.worker.reindex

eval: ## 检索质量评测（recall@k / MRR / nDCG@k），需已入库文档
	cd $(BACKEND) && $(PY) -m app.eval.run --file ../docs/eval/golden.jsonl --top-k 8

frontend-install: ## 安装前端依赖（在本地或有 node 的机器执行）
	cd $(ROOT)/frontend && npm install

frontend-build: ## 构建前端，产物由 FastAPI 托管
	cd $(ROOT)/frontend && npm run build

fmt: ## ruff format
	cd $(BACKEND) && $(PY) -m ruff format .

lint: ## ruff check
	cd $(BACKEND) && $(PY) -m ruff check .

api-schema: ## 导出 OpenAPI 契约快照
	cd $(BACKEND) && PYTHONPATH=$(BACKEND) $(PY) -m app.export_openapi

clean: ## 清理测试数据与缓存
	find $(BACKEND) -name '__pycache__' -type d -prune -exec rm -rf {} +
	rm -rf $(BACKEND)/.pytest_cache $(BACKEND)/.ruff_cache $(ROOT)/data/_test
	@echo "已清理"
