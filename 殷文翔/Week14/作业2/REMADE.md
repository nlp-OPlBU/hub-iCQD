# 多模态 RAG 项目目录说明

## 当前阶段

已根据用户提供的服务器环境完成目录调整、配置示例与 `AGENTS.md`。本项目仍处于结构阶段：所有业务实现、测试、依赖清单、前端配置、SQL、Shell 和 Nginx 配置均为空白占位，尚不能启动或构建。本次没有连接或修改服务器。

## 环境适配后的技术规划

| 部分 | 规划 |
| --- | --- |
| 前端 | Vue 3 + TypeScript + Vite，构建后交给现有 Nginx |
| HTTP API | FastAPI，规划使用 8001，避开已有 vLLM 的 8000 |
| 解析队列 | 现有 Kafka，独立 PDF Worker；MySQL outbox 负责可靠投递 |
| PDF 解析 | 现有 MinerU CLI：`mineru-kit parse ... --format zip ... --tier advanced` |
| 业务存储 | 现有 MySQL，保存文档、状态、任务、会话、消息与 outbox |
| 检索存储 | 现有 Elasticsearch，先规划 BM25，预留独立 Embedding 后的向量与混合检索 |
| 缓存 | 现有 Redis，辅助缓存与协调，MySQL 保留持久化状态 |
| 模型 | 现有 vLLM `http://localhost:8000/v1`，模型 ID 和视觉能力待实际验证 |
| 文件 | API 与 Worker 共享可访问的数据目录，保存原始 PDF、解析产物和图片 |
| 部署 | 服务器原生进程 + Nginx；不以 Docker、PostgreSQL、Qdrant 为默认依赖 |

Temporal 和 Neo4j 虽在环境材料中列出，但暂不纳入当前功能链路。技术适配依据、软件路径与待确认项见 [服务器环境说明](docs/environment.md)。

## 完整目录结构

下列目录树按实际文件生成。除说明文档、`.env.example` 和 `.gitignore` 外，文件均为空白占位；注释描述后续实现职责。

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
│   │   │   │   ├── documents.py  # 批量上传、列表、状态与重试
│   │   │   │   └── health.py  # 健康检查
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   └── router.py  # 路由注册
│   │   ├── core/  # 统一配置与日志
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── config.py  # 环境变量与路径配置
│   │   │   └── logging.py  # 日志初始化与脱敏
│   │   ├── db/  # MySQL 连接与迁移
│   │   │   ├── migrations/  # 后续数据库迁移文件
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
│   │   │   ├── embedding.py  # 独立向量模型适配，待配置
│   │   │   ├── kafka_producer.py  # Kafka 任务生产与发送确认
│   │   │   ├── llm_client.py  # 现有 vLLM 兼容接口与图像输入
│   │   │   ├── mineru_cli.py  # MinerU CLI 子进程与 ZIP 产物适配
│   │   │   ├── rag.py  # 图文 RAG 编排、回答与来源引用
│   │   │   ├── redis_cache.py  # 有 TTL 的缓存和辅助协调
│   │   │   ├── retrieval.py  # 文档过滤、检索及上下文组织
│   │   │   └── storage.py  # 原文件、ZIP、解析产物和图片存取
│   │   ├── workers/  # 与 API 分开启动的进程
│   │   │   ├── __init__.py  # Python 包标记
│   │   │   ├── outbox_dispatcher.py  # 将 MySQL outbox 可靠投递到 Kafka
│   │   │   └── pdf_consumer.py  # Kafka 消费、MinerU、索引与状态更新
│   │   ├── __init__.py  # Python 包标记
│   │   └── main.py  # FastAPI 应用入口
│   ├── tests/  # 测试占位，尚未编写测试
│   │   ├── __init__.py  # Python 包标记
│   │   ├── test_documents.py  # 上传与状态查询
│   │   ├── test_mineru_cli.py  # 子进程、超时与异常 ZIP
│   │   ├── test_outbox.py  # 事务事件和投递补偿
│   │   ├── test_pdf_consumer.py  # 幂等消费、重试与失败处理
│   │   ├── test_rag.py  # 真实图像传递、回答与来源
│   │   └── test_retrieval.py  # ES 检索、范围过滤与模式切换
│   └── requirements.txt  # Python 依赖（空白）
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
│   ├── package.json  # 前端依赖和命令（空白）
│   ├── tsconfig.json  # TypeScript 配置（空白）
│   └── vite.config.ts  # 构建和开发代理配置（空白）
├── infra/  # 复用服务器服务的项目配置与说明
│   ├── elasticsearch/
│   │   └── index_mapping.json  # 分块、来源与可选向量 mapping（空白）
│   ├── kafka/
│   │   └── topics.sh  # 项目专属任务和失败 Topic（空白）
│   ├── mineru/
│   │   └── README.md  # 现有 MinerU CLI 接入说明
│   ├── mysql/
│   │   └── init.sql  # 项目数据库与初始结构（空白）
│   ├── nginx/
│   │   └── multimodal-rag.conf  # 前端静态资源与业务 API 代理（空白）
│   ├── redis/
│   │   └── README.md  # 现有 Redis 接入与键命名空间
│   └── vllm/
│       └── README.md  # 现有模型接口与图片能力待验证项
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
│   ├── api.md  # 后续接口说明（空白）
│   ├── architecture.md  # 整体组件与任务数据流
│   ├── deployment.md  # 原生部署规划与脚本职责
│   └── environment.md  # 服务器环境适配与待确认信息
└── scripts/  # 开发、环境检查与项目进程入口（空白）
    ├── build_frontend.sh  # 构建前端（空白）
    ├── check_environment.sh  # 服务器只读环境检查（空白）
    ├── dev.ps1  # Windows 开发入口（空白）
    ├── dev.sh  # Linux 开发入口（空白）
    ├── init_mysql.sh  # 项目数据库初始化入口（空白）
    ├── start_api.sh  # 独立启动 API（空白）
    ├── start_dispatcher.sh  # 独立启动 outbox 投递器（空白）
    └── start_worker.sh  # 独立启动 PDF 消费者（空白）
```

## 三个功能的代码位置与数据流

### 1. 批量 PDF 上传和异步解析

`PdfUploader.vue` → `api/routes/documents.py` → `storage.py` 保存文件 → MySQL 事务保存文档、任务和 outbox → `outbox_dispatcher.py` + `kafka_producer.py` → Kafka → `pdf_consumer.py` → `mineru_cli.py` → 解析结果保存与 `chunking.py` → `elasticsearch_store.py` → MySQL 状态更新。

浏览器通过 HTTP 上传，Kafka 只传任务元数据；原始文件留在共享文件存储。队列按至少一次投递设计，消费者需要幂等、超时处理、有限重试与失败记录，不能将数据库写入和 Kafka 发送假定为一个天然原子操作。

### 2. 解析状态展示

| 状态 | 前端文字 | 完成条件 |
| --- | --- | --- |
| `pending` | 待解析 | 文件、文档记录和任务已持久化，等待投递或处理 |
| `processing` | 解析中 | 正在解析、持久化产物或建立检索索引 |
| `completed` | 解析完成 | 产物已保存，并已在当前检索模式下可用 |
| `failed` | 解析失败 | 失败原因已持久化，可按策略重试 |

`DocumentList.vue`、`ParseStatus.vue` 和 `stores/documents.ts` 负责展示与轮询，`repositories/document.py` 负责持久化查询和状态更新。Redis 可缓存查询，不能成为唯一状态来源。

### 3. 多模态 RAG 问答

`ChatInput.vue` 提交文字和可选图片 → `api/routes/chat.py` → `llm_client.py` 辅助理解图片并形成检索文本 → `retrieval.py` 在已完成文档内检索 → `rag.py` 组织上下文与来源 → `llm_client.py` 将问题、用户原图和相关文档图文一起送给视觉模型 → `ChatMessage.vue` 展示 Markdown 图文 → `SourceReferences.vue` 展示来源文档和页码。

当前环境未列出 Embedding 服务，故配置示例使用 BM25，不伪造向量；后续配置真实模型和维度后再启用向量或混合检索。当前只有 vLLM 文本请求示例，必须验证实际图片输入后才开启视觉能力。上述限制是后续联调任务，不代表多模态需求已取消或已完成。

## 开发入口与约定

- [AGENTS.md](AGENTS.md)：模块边界、任务一致性、模型与文件处理、配置及验收要求。
- [.env.example](.env.example)：不含真实密钥的配置结构示例；已提供地址和规划默认值都需要服务器核验。
- [docs/architecture.md](docs/architecture.md)：组件关系和异步任务链路。
- [docs/deployment.md](docs/deployment.md)：复用现有服务器环境的部署顺序和脚本职责。
- [docs/api.md](docs/api.md)：后续接口文档占位。

业务代码尚未编写，不执行空白脚本或安装空白依赖清单，也不把目录检查当作功能测试通过。服务器材料中的原始密码没有复制进本项目。
