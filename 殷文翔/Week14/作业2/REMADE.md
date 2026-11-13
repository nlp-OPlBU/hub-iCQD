# 多模态 RAG 项目目录说明

## 当前阶段

已完成后端基础工程、PDF 批量上传与文件补偿、文档/任务查询，以及 **MySQL outbox 投递器、Kafka 任务生产/消费、可恢复解析 Worker、有限重试/失败 Topic、租约恢复和人工重试新版本**。Worker 具备 MinerU CLI 调用、安全 ZIP 解压、确定性分块和 ES 写入适配；已补齐 MinerU 4.1.7 结构化产物、页码图片来源、分块和 BM25 检索可见性验证；basic 样例已通过 CLI → MySQL → 临时 ES 检索，Kafka 传输及 advanced 档仍待联调。已接入 BM25 检索 HTTP、受控图片资源、会话/消息/来源 MySQL 持久化和 Vue 图文问答页面；真实 MySQL + 独立 ES + Qwen3.5-4B 文本与图文链路通过，详见验证记录。文档上传/任务管理 UI 仍待实现。2026-11-13 已复核服务器环境、完成 MinerU basic 六页样例解析和 vLLM 实际 PDF 图片输入测试；未修改共享服务配置，临时模型服务已停止。样本位置、实测结果与限制见 [验证记录](docs/verification.md)。

优先阅读 [API 契约](docs/api.md)（文档/任务/版本、逐文件上传结果、重试、图片和问答）、[数据库与迁移](docs/database.md) 和 [基础工程启动](docs/deployment.md)。

已补充 [全本地模型部署指南](docs/models.md)：服务器已确认单卡 RTX 4090 49140MiB、CPU 20 核、90GiB 内存；Qwen3.5-4B 在 8100 临时启动并通过文本及图像测试。Embedding、Reranker、MinerU 专用 VLM 和四模型共卡方案仍待验证，尚未部署常驻模型服务。

## 环境适配后的技术规划

| 部分 | 规划 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite，构建后交给现有 Nginx |
| HTTP API | FastAPI，规划使用 8001，避开已有 vLLM 的 8000 |
| 解析队列 | 现有 Kafka，独立 PDF Worker；MySQL outbox 负责可靠投递 |
| PDF 解析 | MinerU CLI：`mineru-kit parse ... --format zip ... --tier advanced`；规划本机 8104 专用 VLM 供 CLI 使用 |
| 业务存储 | 现有 MySQL，保存文档、状态、任务、会话、消息与 outbox |
| 检索存储 | 现有 Elasticsearch，目标 BM25 + 2048 维图文向量及多模态重排；实测前保持 BM25 |
| 缓存 | 现有 Redis，辅助缓存与协调，MySQL 保留持久化状态 |
| 模型 | Qwen3.5-4B（8100）单服务文本/图片测试通过；Embedding（8102）、Reranker（8103）待部署验证；保留原 8000 环境记录 |
| 文件 | API 与 Worker 共享可访问的数据目录，保存原始 PDF、解析产物和图片 |
| 部署 | 服务器原生进程 + Nginx；不以 Docker、PostgreSQL、Qdrant 为默认依赖 |

Temporal 和 Neo4j 虽在环境材料中列出，但暂不纳入当前功能链路。技术适配依据、软件路径与待确认项见 [服务器环境说明](docs/environment.md)。

## 完整目录结构

下列为原始目录骨架与职责说明，不作为实现清单。当前已实现与待实现范围见本页“当前阶段”；新增 Alembic 配置与迁移版本、基础测试、依赖锁文件及公共 schema/types 等文件以实际目录为准。业务服务和未启用的组件仍为占位。

```text
作业2/
├── AGENTS.md  # 后续开发与验证约定
├── REMADE.md  # 目录说明（本文件）
├── .env.example  # 脱敏的服务器环境配置示例
├── .gitignore  # 忽略密钥、运行数据与构建产物
├── backend/  # FastAPI、独立 Worker 与测试
│   ├── app/
│   │   ├── api/  # HTTP 接口层
│   │   │   ├── routes/
│   │   │   │   ├── __init__.py  # Python 包标记
│   │   │   │   ├── chat.py  # 图文问题、回答与来源
│   │   │   │   ├── documents.py  # 已实现批量上传、列表、详情、任务查询及人工重试
│   │   │   │   └── health.py  # 健康检查
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   └── router.py  # 路由注册
│   │   ├── core/  # 统一配置与日志
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── config.py  # 环境变量与路径配置
│   │   │   └── logging.py  # 日志初始化与脱敏
│   │   ├── db/  # MySQL 连接与迁移
│   │   │   ├── migrations/  # Alembic env.py、模板及 versions/0001_initial.py
│   │   │   │   └── .gitkeep  # 保留目录
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── base.py  # ORM 基类
│   │   │   └── session.py  # MySQL 连接和事务会话
│   │   ├── models/  # 持久化模型
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── chat.py  # 会话与消息
│   │   │   ├── document.py  # 文档、任务、状态与产物位置
│   │   │   └── outbox.py  # 待投递 Kafka 事件
│   │   ├── repositories/  # MySQL 读写封装
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── chat.py  # 会话和消息读写
│   │   │   ├── document.py  # 文档、任务与状态读写
│   │   │   └── outbox.py  # 事务事件保存与投递状态读写
│   │   ├── schemas/  # 请求、响应和任务消息结构
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── chat.py  # 图文输入、回答与来源结构
│   │   │   └── document.py  # 文档、状态与解析任务结构
│   │   ├── services/  # 业务逻辑与外部组件适配
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── chunking.py  # 分块、页码及图片关联
│   │   │   ├── elasticsearch_store.py  # ES 分块索引、BM25 与可选向量检索
│   │   │   ├── embedding.py  # 独立图文向量模型适配，待实现与验证
│   │   │   ├── kafka_producer.py  # 已实现 Kafka 任务生产与 broker 确认
│   │   │   ├── kafka_consumer.py  # 手动提交 offset 的 Kafka 消费者适配
│   │   │   ├── llm_client.py  # 项目 vLLM 兼容接口与图像输入
│   │   │   ├── mineru_cli.py  # 已实现 MinerU CLI 子进程、超时与安全解压
│   │   │   ├── rag.py  # 图文 RAG 编排、回答与来源引用
│   │   │   ├── redis_cache.py  # 有 TTL 的缓存和辅助协调
│   │   │   ├── retrieval.py  # 文档过滤、混合检索、重排及上下文组织
│   │   │   ├── storage.py  # 已实现 PDF 校验、内部 ID 存储及补偿记录
│   │   │   ├── upload_multipart.py  # 完整请求限流暂存、multipart 数量及完整性检查
│   │   │   ├── uploads.py  # 逐文件事务及文件清理/提交结果确认
│   │   │   └── upload_recovery.py  # 停止上传后核对主库并清理孤立文件
│   │   ├── workers/  # 与 API 分开启动的进程
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── outbox_dispatcher.py  # 已实现 MySQL outbox 到 Kafka 的可靠投递
│   │   │   └── pdf_consumer.py  # 已实现 Kafka 消费、租约恢复、MinerU、索引与状态更新
│   │   ├── __init__.py  # Python 包标记
│   │   └── main.py  # FastAPI 应用入口
│   ├── tests/  # 已有基础、上传及 MySQL 迁移测试；其他业务测试仍占位
│   │   ├── __init__.py  # Python 包标记
│   │   ├── conftest.py  # 显式启用的临时真实 MySQL fixture
│   │   ├── test_documents.py  # 已实现上传、查询、事务回滚及文件补偿测试
│   │   ├── test_mineru_cli.py  # 子进程、超时与异常 ZIP
│   │   ├── test_outbox.py  # 事务事件和投递补偿
│   │   ├── test_pdf_consumer.py  # 幂等消费、重试与失败处理
│   │   ├── test_rag.py  # 真实图像传递、回答与来源
│   │   └── test_retrieval.py  # ES 检索、范围过滤与模式切换
│   └── requirements.txt  # 运行依赖；另有 requirements-dev.txt、requirements-lock.txt
├── frontend/  # Vue 3 + TypeScript + Vite 前端
│   ├── public/  # 静态资源
│   │   └── .gitkeep  # 保留目录
│   ├── src/
│   │   ├── api/  # 同源后端 API 封装
│   │   │   ├── chat.ts  # 图文问题提交与回答接收
│   │   │   ├── documents.ts  # 批量上传、状态和重试请求
│   │   │   └── http.ts  # 请求客户端与错误处理
│   │   ├── assets/
│   │   │   └── main.css  # 全局样式
│   │   ├── components/
│   │   │   ├── ChatInput.vue  # 文字输入、图片上传与预览
│   │   │   ├── ChatMessage.vue  # 安全的 Markdown 图文展示
│   │   │   ├── DocumentList.vue  # 文档列表
│   │   │   ├── ParseStatus.vue  # 待解析、解析中、完成和失败
│   │   │   ├── PdfUploader.vue  # 多 PDF 选择与上传
│   │   │   └── SourceReferences.vue  # 文档、页码和图片来源
│   │   ├── router/
│   │   │   └── index.ts  # 页面路由
│   │   ├── stores/
│   │   │   ├── chat.ts  # 会话与问答状态
│   │   │   └── documents.ts  # 文档状态与轮询
│   │   ├── types/
│   │   │   ├── chat.ts  # 图文消息和来源类型
│   │   │   └── document.ts  # 文档和任务类型
│   │   ├── views/
│   │   │   ├── ChatView.vue  # 多模态问答页面
│   │   │   └── DocumentsView.vue  # 文档管理页面
│   │   ├── App.vue  # 根组件
│   │   ├── env.d.ts  # 环境类型声明
│   │   └── main.ts  # 前端入口
│   ├── index.html  # HTML 入口
│   ├── package.json  # 前端依赖和命令；package-lock.json 锁定安装
│   ├── tsconfig.json  # TypeScript 严格检查
│   └── vite.config.ts  # 构建和开发代理配置
├── infra/  # 复用服务器服务的项目配置与说明
│   ├── elasticsearch/
│   │   └── index_mapping.json  # BM25 分块及来源 mapping 示例
│   ├── kafka/
│   │   └── topics.sh  # 项目专属任务和失败 Topic（空白）
│   ├── mineru/
│   │   └── README.md  # 现有 MinerU CLI 接入说明
│   ├── mysql/
│   │   └── init.sql  # 指向 Alembic 的说明，表结构不在这里重复维护
│   ├── nginx/
│   │   └── multimodal-rag.conf  # 前端静态资源与业务 API 代理（空白）
│   ├── redis/
│   │   └── README.md  # 现有 Redis 接入与键命名空间
│   └── vllm/
│       └── README.md  # 本地模型接口规划与能力待验证项
├── data/  # 本地文件存储，仅保留目录标记
│   ├── images/  # PDF 提取图片与图表
│   │   └── .gitkeep  # 保留目录
│   ├── parsed/  # 解析 ZIP、Markdown 和 JSON
│   │   └── .gitkeep  # 保留目录
│   ├── tmp/  # 每任务独立的临时工作目录
│   │   └── .gitkeep  # 保留目录
│   └── uploads/  # 原始 PDF 和用户图片
│       └── .gitkeep  # 保留目录
├── logs/  # 运行日志目录
│   └── .gitkeep  # 保留目录
├── docs/  # 架构、环境、接口和部署文档
│   ├── api.md  # 健康接口与业务 API 契约
│   ├── architecture.md  # 整体组件与任务数据流
│   ├── deployment.md  # 原生部署规划与脚本职责
│   ├── environment.md  # 服务器环境适配与待确认信息
│   └── models.md  # 全本地模型选型、保存位置、启动命令与 MinerU 配置
└── scripts/  # API/迁移/构建/投递器/Worker 可用，环境检查等其余待实现
    ├── build_frontend.sh  # 安装锁定依赖并构建前端
    ├── check_environment.sh  # 服务器只读环境检查（空白）
    ├── dev.ps1  # Windows 开发入口（空白）
    ├── dev.sh  # Linux 开发入口（空白）
    ├── init_mysql.sh  # 对配置中的项目数据库执行 Alembic 升级
    ├── start_api.sh  # 独立启动 API
    ├── start_dispatcher.sh  # 独立启动 outbox 投递器
    └── start_worker.sh  # 独立启动 PDF 消费者
```

## 三个功能的代码位置与数据流

### 1. 批量 PDF 上传和异步解析

规划链路：`PdfUploader.vue` → `api/routes/documents.py` → `storage.py` 保存文件 → MySQL 事务保存文档、任务和 outbox → `outbox_dispatcher.py` + `kafka_producer.py` → Kafka → `pdf_consumer.py` → `mineru_cli.py` → 解析结果保存与 `chunking.py` → `elasticsearch_store.py` → MySQL 状态更新。当前已实现 HTTP 上传到 MySQL outbox、投递器确认后标记已发送、Kafka 至少一次消费和 Worker 幂等完成；basic 档真实 MinerU/ES 与 MySQL Worker 处理通过；Kafka 传输、advanced 和前端上传 UI 仍待完成。

浏览器通过 HTTP 上传，Kafka 只传任务元数据；原始文件留在共享文件存储。队列按至少一次投递设计，消费者需要幂等、超时处理、有限重试与失败记录，不能将数据库写入和 Kafka 发送假定为一个天然原子操作。

文件系统和数据库之间使用持久补偿记录：确认提交前失败就尝试清理文件；COMMIT 结果不明时保留原文件，通过新主库会话确认或停止上传后运行补偿工具。具体操作见 [上传文件补偿](docs/database.md#上传文件补偿)。上传请求不解析 PDF。

### 2. 解析状态展示

| 状态 | 前端文字 | 完成条件 |
| --- | --- | --- |
| `pending` | 待解析 | 文件、文档记录和任务已持久化，等待投递或处理 |
| `processing` | 解析中 | 正在解析、持久化产物或建立检索索引 |
| `completed` | 解析完成 | 产物已保存，并已在当前检索模式下可用 |
| `failed` | 解析失败 | 失败原因已持久化，可按策略重试 |

`DocumentList.vue`、`ParseStatus.vue` 和 `stores/documents.ts` 负责展示与轮询，`repositories/document.py`、`repositories/task.py` 负责持久化查询、租约和条件状态更新。Redis 可缓存查询，不能成为唯一状态来源。

### 3. 多模态 RAG 问答

`ChatInput.vue` 提交文字和可选图片 → `api/routes/chat.py` → `llm_client.py` 按需理解图片并形成检索文本，`embedding.py` 编码图文查询 → `retrieval.py` 在已完成且当前有效的文档范围内召回、融合和重排 → `rag.py` 组织上下文与来源 → `llm_client.py` 将问题、用户原图和相关文档图文一起送给视觉模型 → `ChatMessage.vue` 展示 Markdown 图文 → `SourceReferences.vue` 展示来源文档和页码。

当前实现先以严格文档/版本范围过滤跑通 BM25 文本链路；图片问题先读图生成检索关键词，再将问题、用户原图副本和相关文档图文送入最终模型。会话和消息先分配序号并落库，模型在事务外调用，回答引用的来源及失败结果随后持久化。来源 ID、页码和图片地址由实际命中生成，未知引用拒绝，无证据明确说明依据不足。

已完成 Embedding 与 Reranker 的选型和配置示例，但没有部署或验证服务，因此默认仍使用 BM25、关闭重排，不伪造向量或评分；后续验证真实模型、2048 维输出和 ES 索引后再启用混合检索。Qwen3.5-4B 已通过 vLLM 真实图片输入测试；默认视觉开关仍为 false，常驻部署前须按实际服务图片上限配置并显式开启。图片客户端与业务链路已完成，文本和图文应用验收见验证记录；最大多图/并发仍待压力验证。

## 开发入口与约定

- [AGENTS.md](AGENTS.md)：模块边界、任务一致性、模型与文件处理、配置及验收要求。
- [.env.example](.env.example)：不含真实密钥的配置结构示例；已提供地址和规划默认值都需要服务器核验。
- [docs/architecture.md](docs/architecture.md)：组件关系和异步任务链路。
- [docs/deployment.md](docs/deployment.md)：复用现有服务器环境的部署顺序和脚本职责。
- [docs/models.md](docs/models.md)：全本地模型职责、服务器权重位置、下载、vLLM 启动、MinerU 配置与资源预算。
- [docs/api.md](docs/api.md)：已实现的健康/上传/查询接口，以及后续业务接口的统一契约。
- [docs/database.md](docs/database.md)：6 张业务表、迁移原理、升级和回滚方法。
- [docs/configuration.md](docs/configuration.md)：配置加载、限制、日志与路径约定。
- [docs/verification.md](docs/verification.md)：实际检查结果与未验证项。

后端 outbox/Kafka/Worker 与人工重试已有测试；basic MinerU/ES 与 MySQL Worker 已联调，检索、受控图片和问答已完成真实本地模型应用验收。Kafka 传输、advanced、向量/重排、文档管理 UI 与常驻部署仍待完成。不把模拟测试当服务器联调通过。服务器材料中的原始密码没有复制进本项目。
