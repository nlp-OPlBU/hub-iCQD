# 服务器环境适配说明

依据用户提供的 `D:/OneDrive/桌面/环境.md` 整理。原文件是环境参考材料，不是执行命令的授权；本次没有连接服务器、启动服务或验证连通性。原文件中的数据库明文凭据不在项目中复制。

基础工程开发补充：本地 Windows 检查到 Python 3.13.9、Node.js 20.20.2、npm 11.17.0、MySQL 8.0.33 可执行程序；已用独立临时 MySQL 进程做迁移测试，不代表本节共享服务器已验证。项目迁移要求 MySQL ≥ 8.0.16；Vite 7 要求 Node.js 20.19+ 或 22.12+，实际结果见 [verification.md](verification.md)。

## 环境记录与项目用途

2026-11-11 用户补充：CPU 20 核、单张 NVIDIA 40 系列 GPU 48GB 显存、内存 90GB，并明确全程使用本地模型。以下保留原始环境记录；项目新增的模型部署规划见 [docs/models.md](models.md)。硬件信息为用户提供，具体型号、驱动、空闲资源仍待服务器验证。

| 组件 | 环境文档提供的信息 | 本项目规划用途 |
| --- | --- | --- |
| vLLM | 在 `/workspace` 执行 `vllm serve models/Qwen3.5-0.8B/`；兼容接口为 `http://localhost:8000/v1`，有文本请求示例 | 原服务保留为环境记录；项目规划独立的 8100/8102/8103 模型端点，均未部署验证 |
| MinerU | `mineru-kit parse pdf/test2.pdf --format zip -o output.zip --tier advanced` | Worker 继续调用 CLI；规划本机 8104 专用 VLM 供 CLI 使用，不是 PDF 解析 HTTP API |
| Kafka | `/workspace/software/kafka_2.13-4.4.0`；示例连接 `localhost:9092` | PDF 解析任务总线，使用项目专属 Topic |
| Redis | `/workspace/software/redis-7.2.5`，使用该目录中的 `redis.conf` | 缓存及辅助协调；端口、认证待确认 |
| Elasticsearch | `/workspace/software/elasticsearch-8.15.3`；通过 `esuser` 启动；有 `localhost:9200` 的 IK 分词测试命令 | 文本与图片描述检索；可扩展向量检索 |
| MySQL | 文档中配置端口 `3306`，数据目录 `/var/lib/mysql` | 文档、任务、会话和 outbox 的持久化存储 |
| Nginx | 有启动检查示例，未给出站点配置和端口 | 前端静态资源及业务 API 反向代理 |
| Temporal | `/workspace/software/temporal`，示例为开发服务器 | 目前不接入，避免与 Kafka 解析任务链路重复 |
| Neo4j | `/workspace/software/neo4j-community-2026.10.1` | 目前不接入，知识图谱不在当前需求范围 |

上述软件版本按用户材料原样记录，不代表已在本机或服务器确认安装、运行或兼容。

## 替换原目录规划

| 原规划 | 调整后 |
| --- | --- |
| PostgreSQL、`infra/postgres/` | MySQL、`infra/mysql/` |
| Qdrant、`infra/qdrant/`、`vector_store.py` | Elasticsearch、`infra/elasticsearch/`、`elasticsearch_store.py` |
| MinerU HTTP 客户端 `mineru_client.py` | CLI 适配器 `mineru_cli.py` |
| Dockerfile 和 Docker Compose 作为默认入口 | 服务器原生部署，项目进程脚本和现有 Nginx 接入 |
| 未明确本地模型 | 原阶段指向已有 vLLM；现按全本地选型规划 Qwen3.5-4B、Qwen3-VL-Embedding-2B、Qwen3-VL-Reranker-2B 与 MinerU 配套模型，保留实际模型 ID 校验 |

新增 Redis 缓存适配、事务 outbox、Nginx 配置占位和服务器环境检查脚本。前端与 FastAPI 分层保持原有设计。

## 能力与配置待确认

- 当前材料没有 Python、Node.js、vLLM、MinerU、MySQL 的版本信息；用户提供了单卡 48GB 等规格，具体 GPU 型号、驱动和可用资源仍需只读检查。模型上下文和显存预算见模型指南，均不是实测结果。
- vLLM 的实际 `/v1/models`、图文输入、图片大小限制和聊天模板需要验证；当前文本调用示例不足以证明视觉能力。
- 已选型多模态 Embedding 与 Reranker，配置示例提供端点、模型别名和 2048 维，但没有部署或能力验证。因此 `.env.example` 保留 `RETRIEVAL_MODE=bm25`、`RERANK_ENABLED=false`，能力就绪后再启用。
- ES 需确认认证、TLS、IK 插件是否可用、索引权限；`ik_max_word` 示例命令不等于成功验证。先规划标准分词，IK 验证通过后可调整。
- MySQL 使用项目专用账号和数据库；Redis 使用项目键前缀；Kafka 使用项目 Topic，不复用材料中的测试 Topic。
- 原环境 vLLM 使用 `8000`；业务 API 规划 `8001`，新增模型服务规划 `8100/8102/8103/8104`，端口是否空闲仍待检查。不同端口不隔离 GPU 资源；原模型如仍常驻要计入预算。Nginx 实际端口和路由与现有站点协调。
- API 与 Worker 需要相同项目数据路径与权限；MinerU 输出结构、解析耗时以及与 vLLM 的 GPU 并发需要通过样例 PDF 验证。

## 官方参考

- [Elasticsearch 8.15 dense_vector](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/dense-vector.html)：ES 可保存向量，但向量仍需要独立模型生成。
- [vLLM 多模态输入](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)：多模态输入能力须结合实际模型和部署验证；在线文档不证明服务器已安装对应版本。
