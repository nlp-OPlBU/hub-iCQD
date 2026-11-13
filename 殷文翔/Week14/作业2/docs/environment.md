# 服务器环境适配说明

依据用户提供的 `D:/OneDrive/桌面/环境.md` 整理。原文件是环境参考材料，不是执行命令的授权。2026-11-12、2026-11-13 已按用户授权检查服务器、解析样例并验证图片输入；最新结果见“服务器实测补充（2026-11-13）”。未修改共享服务配置，临时项目模型测试进程已停止。原文件中的数据库明文凭据不在项目中复制。

基础工程开发补充：本地 Windows 检查到 Python 3.13.9、Node.js 20.20.2、npm 11.17.0、MySQL 8.0.33 可执行程序；已用独立临时 MySQL 进程做迁移测试，不代表本节共享服务器已验证。项目迁移要求 MySQL ≥ 8.0.16；Vite 7 要求 Node.js 20.19+ 或 22.12+，实际结果见 [verification.md](verification.md)。

## 环境记录与项目用途

2026-11-11 用户补充 CPU 20 核、单张 NVIDIA 40 系列 GPU 48GB 显存、内存 90GB，并明确全程使用本地模型；2026-11-13 已实测确认具体硬件，见下文。以下保留原始环境记录；项目新增的模型部署规划见 [docs/models.md](models.md)。

| 组件 | 环境文档提供的信息 | 本项目规划用途 |
| --- | --- | --- |
| vLLM | 在 `/workspace` 执行 `vllm serve models/Qwen3.5-0.8B/`；兼容接口为 `http://localhost:8000/v1`，有文本请求示例 | 原服务保留为环境记录；8100 单服务图文测试通过，8102/8103 尚未验证 |
| MinerU | `mineru-kit parse pdf/test2.pdf --format zip -o output.zip --tier advanced` | Worker 继续调用 CLI；规划本机 8104 专用 VLM 供 CLI 使用，不是 PDF 解析 HTTP API |
| Kafka | `/workspace/software/kafka_2.13-4.4.0`；示例连接 `localhost:9092` | PDF 解析任务总线，使用项目专属 Topic |
| Redis | `/workspace/software/redis-7.2.5`，使用该目录中的 `redis.conf` | 缓存及辅助协调；端口、认证待确认 |
| Elasticsearch | `/workspace/software/elasticsearch-8.15.3`；通过 `esuser` 启动；有 `localhost:9200` 的 IK 分词测试命令 | 文本与图片描述检索；可扩展向量检索 |
| MySQL | 文档中配置端口 `3306`，数据目录 `/var/lib/mysql` | 文档、任务、会话和 outbox 的持久化存储 |
| Nginx | 有启动检查示例，未给出站点配置和端口 | 前端静态资源及业务 API 反向代理 |
| Temporal | `/workspace/software/temporal`，示例为开发服务器 | 目前不接入，避免与 Kafka 解析任务链路重复 |
| Neo4j | `/workspace/software/neo4j-community-2026.10.1` | 目前不接入，知识图谱不在当前需求范围 |

本表保留材料来源；软件安装版本、运行状态与兼容性以文末有日期的实测记录为准。

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

- 已实测 Python、Node.js、vLLM、MinerU、MySQL 客户端、GPU 和驱动版本，见下文。模型指南中的四服务共卡预算、峰值显存与并发仍需验证。
- vLLM 8100 的实际 `/v1/models` 与真实图片输入已验证；大图、多图上限、长上下文和常驻部署稳定性仍需验证。
- 已选型多模态 Embedding 与 Reranker，配置示例提供端点、模型别名和 2048 维，但没有部署或能力验证。因此 `.env.example` 保留 `RETRIEVAL_MODE=bm25`、`RERANK_ENABLED=false`，能力就绪后再启用。
- ES 文件配置已确认关闭 security/TLS，analysis-ik 已安装；服务未监听，运行时认证、IK 分词与索引权限仍待验证。先规划标准分词。
- MySQL 使用项目专用账号和数据库；Redis 使用项目键前缀；Kafka 使用项目 Topic，不复用材料中的测试 Topic。
- 8000、8001、8100/8102/8103/8104 在测试结束后均未监听，8001 临时 bind 成功；部署时仍须复核。不同端口不隔离 GPU 资源。Nginx 实际端口和路由与现有站点协调。
- API 与 Worker 需要相同项目数据路径与权限；目录当前为 root:root 755，非 root 部署账号的写权限待安排。MinerU basic 输出结构已验证；advanced 档及与 vLLM 的 GPU 并发仍待验证。

## 服务器实测补充（2026-11-13）

仅对共享服务做只读检查；解析产物写入项目独立测试目录，临时启动的 8100 项目模型已停止。完整证据与样本位置见 [verification.md](verification.md)。本节覆盖下方 2026-11-12 的即时状态结论。

| 项目 | 最新实测结果 |
| --- | --- |
| 系统 | Ubuntu 22.04.5，内核 6.12.115-compshare，20 核，90GiB 内存；`/workspace` 可用约 23GiB |
| 软件版本 | py312 Python 3.12.11；MinerU 4.1.7；vLLM 0.25.1；PyTorch 2.11.0（CUDA 13.0）；Transformers 5.14.1；Node v22.23.5；npm 10.9.10 |
| 其他软件 | MySQL 客户端 8.0.46；Kafka 安装包 2.13-4.4.0；Redis 7.2.5；ES 安装包 8.15.3（analysis-ik 已安装）；Nginx 1.18.0 |
| 连通性 | 3306/9092/6379/9200/80 均连接失败、未监听；本次未启动共享服务。MySQL 服务端版本和账号登录不能以客户端版本或历史成功替代 |
| 认证 | Kafka 配置为 PLAINTEXT、未设 authorizer；Redis 配置仅绑定本机、protected-mode=yes，未见 requirepass/ACL 配置；ES 配置关闭 security 和 HTTP TLS；Nginx 已检查配置未见 basic-auth 指令。均未完成运行时认证联调 |
| MySQL 账号 | 项目账号和密码已配置；连接返回错误码 2003（服务不可达），本次未进入认证阶段，不判定密码正确或错误 |
| 8001 | 未监听，本机临时 bind 成功并立即关闭；本次未启动 API，未重测 API health/ready |
| 目录 | `data/{tmp,uploads,parsed,images}`、`logs`、`runtime`、`models`、`mineru` 均为 root:root 755；当前 root 可读写；非 root 写入不能据此推定可用 |
| GPU | RTX 4090，49140MiB，驱动 615.78.08；存在 `/dev/nvidia5`、nvidiactl、UVM；torch CUDA 初始化和 GPU 推理通过。虽仍有 `NVIDIA_VISIBLE_DEVICES=void`，实测 CUDA 可用，不能只按该变量判定失败 |
| MinerU | 六页 `test2.pdf` basic CPU 解析成功，33.24 秒；ZIP 文本、页码及 11 张图片关联通过；8104 未监听，advanced 本轮未重跑 |
| vLLM | 8100 临时服务 `/health`、`/v1/models` 和真实 PDF 图片请求均为 200；实际模型 ID `rag-vlm`，权重 Qwen3.5-4B；正确读出“问答技能”“100件” |
| 清理 | 临时模型退出码 0，8100 已释放，GPU 回到 1MiB 占用；无模型常驻部署 |

已取得可分析的 MinerU basic 样本，并确认主模型 vLLM 真实视觉输入可用；GPU 与主模型视觉能力的旧阻塞已解除。完整多模态 RAG 验收仍需共享服务恢复、MinerU 8104/advanced、Embedding/Reranker、ES 与应用问答链路联调。应用视觉开关仍为 false，检索保持 BM25、关闭重排。本次 `pip check` 仍报告 MinerU 要求 `openai<3` 与已装 3.48.0 冲突；解析和上述推理成功不代表依赖冲突已修复。

## 服务器实测补充（2026-11-12）

在服务器项目目录执行，未修改共享服务配置。详细记录见 [verification.md](verification.md)。

| 项目 | 实测结果 |
| --- | --- |
| 系统 | Ubuntu 22.04.5，内核 6.12.115，20 核，90GB 内存，`/workspace` 可用约 26GB |
| Python / 依赖 | py312 Python 3.12.11；MinerU 4.1.7；vLLM 0.25.1 |
| Node/npm | Node v22.23.5，npm 10.9.9；前端构建通过 |
| MySQL | 8.0.46；`127.0.0.1:3306` 可连，项目账号认证成功，业务表已存在 |
| 其他服务 | Kafka 2.13-4.4.0、Redis 7.2.5、ES 8.15.3（含 IK）、Nginx 1.18.0 已安装但均未启动；9092/6379/9200/80 未监听 |
| 认证 | Kafka PLAINTEXT 无认证；Redis 仅本机、未设 requirepass；ES security 关闭；MySQL 项目账号可用 |
| 8001 | 未监听；临时启动 API 后 health、ready、openapi 均为 200，随后停止并释放端口 |
| GPU | nvidia-smi 可见 RTX 4090 49140MiB，但容器 `NVIDIA_VISIBLE_DEVICES=void`，CUDA `cuInit` 失败，torch 不可用 |
| MinerU 样例 | `test2.pdf` 6 页 `basic` 档 CPU 解析成功；ZIP 含 Markdown、JSON 和 11 张图片，页码/图片关联完整 |
| MinerU 高级档 | `advanced` 因 8104 未启动失败 |
| vLLM | 8000/8100/8102/8103/8104 均未监听，实际模型 ID 未验证；CPU transformers 回退读图成功，但不等于 vLLM 验收通过 |

当日 GPU 不可用曾阻塞多模态链路；2026-11-13 CUDA 和 8100 图片输入已通过，以上 GPU/模型失败结论仅作历史记录。8104 和其他共享服务仍待部署联调；上传、文本检索与 BM25 开发可继续。

## 官方参考

- [Elasticsearch 8.15 dense_vector](https://www.elastic.co/guide/en/elasticsearch/reference/8.15/dense-vector.html)：ES 可保存向量，但向量仍需要独立模型生成。
- [vLLM 多模态输入](https://docs.vllm.ai/en/stable/features/multimodal_inputs/)：多模态输入能力须结合实际模型和部署验证；在线文档不证明服务器已安装对应版本。

## MinerU/BM25 应用接入补充（2026-11-13）

本轮项目 MySQL 3306 已可访问，成功保存样例 completed 与三类故障 failed。独立临时 ES 8.15.3 使用 19200/19300、独立配置及数据目录完成 standard/BM25 检索，随后停止；没有覆盖共享 ES 配置或启动共享 9200。basic 六页样例的 CLI、来源转换、索引与 MySQL 状态链路通过；advanced/8104、Kafka 实际投递、共享服务常驻和非 root Worker 权限仍待验证。详细数据与复验脚本见 [验证记录](verification.md) 顶部。上文端口不可达为较早检查时的状态。

## 检索与多模态问答应用实测（2026-11-13）

本轮完成 BM25 文本与用户图片问答的真实应用验收：独立临时 MySQL（随机本机端口）、ES 8.15.3（19200/19300）和 Qwen3.5-4B vLLM（8100）通过，过程与证据见 [verification.md](verification.md) 顶部。没有改动共享服务配置或 .env；独立测试服务均已停止，8100/19200/19300 释放、GPU 回到 1MiB。应用视觉开关默认仍为 false，部署时显式开启并核对实际模型图片上限。

Python 项目新增运行依赖 Pillow 12.3.0 和 httpx 0.28.1，均已在 py312 中存在，无须改动 MinerU/vLLM 的安装。前端新增 marked、DOMPurify 及 Playwright 开发检查，锁文件已更新。Chromium 验证所需的 libgbm/wayland-server 仅以 .deb 解压副本放在项目 runtime 并通过 LD_LIBRARY_PATH 使用，未改系统包或共享服务。常驻部署、最大多图与并发压力、advanced/8104、Embedding/Reranker 仍待验证。

## 完整异常矩阵与项目独立部署（2026-11-13）

本轮复核 Kafka 9092 与 ES 9200 已在线，可通过真实管理/检索请求；仅创建和使用项目专属 Topic/索引，没有修改或重启共享服务。因共享 MySQL 3306 未监听，项目使用全新私有原生实例 13306（--no-defaults、独立数据/socket/PID、项目业务账号和随机密码）；没有启动共享 MySQL。API 8001、独立 Nginx 18080、Qwen 8100 与 MinerU 专用 VLM 8104 已运行，原 .env 未改动，实际配置在忽略目录 runtime/deployment/project.env，权限 0600。

真实 Kafka 完整链路和重复消费、补偿、强杀恢复、超时、ES 失败、旧版本隔离及真实图片输入均通过。advanced 六页 PDF 约 62 秒完成，6 页、14 张图片关联，检索命中实际第 2 页；8104 的实际 ID 为 mineru-vlm、有效上下文 8192，原规划 16384 被当前 vLLM 拒绝，现已修正。两个模型同时驻留 GPU 约 24005MiB / 49140MiB；解析小模型本轮使用 CPU。非 root、开机托管、四模型共卡、高并发、鉴权和公网入口未验收；共享 Nginx 配置未加载。可复现步骤、进程状态、测试记录和其他限制见 [服务器验收](server-acceptance.md) 及 [验证记录](verification.md) 顶部，本节覆盖此前的服务离线快照。
