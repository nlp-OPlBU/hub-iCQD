# 系统架构规划

当前已实现配置、日志、HTTP 健康检查、ORM 与 MySQL 迁移、PDF 批量上传与文档/任务查询、可构建的前端入口；下列 Kafka 投递、解析、检索和问答链路仍为设计。

```text
浏览器（Vue）
  └─ Nginx
      ├─ 前端构建静态文件
      └─ /api/ → FastAPI（规划 127.0.0.1:8001）
                   ├─ 共享文件存储：原始 PDF、用户图片与解析产物
                   ├─ MySQL：文档、任务、会话、消息、outbox
                   ├─ Elasticsearch：分块、图片说明、来源与可选向量
                   ├─ Redis：缓存与辅助协调
                   ├─ vLLM 8100：Qwen3.5-4B 图文理解与回答（待验证）
                   ├─ vLLM 8102：Qwen3-VL-Embedding-2B 图文向量（待验证）
                   └─ vLLM 8103：Qwen3-VL-Reranker-2B 重排（待验证）

MySQL outbox → 独立投递器 → Kafka → PDF Worker
                                         ├─ MinerU CLI（小模型 + 本机 8104 专用 VLM）
                                         │     └─ ZIP → Markdown / JSON / 图片
                                         ├─ 分块 → BM25 索引 + 经验证的图文向量 → ES
                                         └─ 更新 MySQL 中的处理状态
```

上传已在同一个 MySQL 事务中写入文档、v1 pending 任务与 outbox 事件；文件保存和数据库提交之间通过持久补偿记录衔接，明确回滚时清理、提交结果不明时保留并核对，见 [补偿操作](database.md#上传文件补偿)。上传只做大小/数量/容器标记校验，不解析 PDF。后续独立投递器向 Kafka 发布、PDF 消费者按任务 ID 和版本幂等处理，均待实现；文件保存、索引和状态更新均完成后才能标记 `completed`。失败记录、重试及 offset 处理需要在实现时保证可恢复。

前端轮询 MySQL 驱动的状态接口；Redis 仅作为可失效缓存。问答先限定已完成且当前有效的文档范围，再通过 BM25 与图文向量召回、融合去重、多模态 Reranker 选取证据；用户原图以及选取的文档图片通过视觉输入传给 Qwen3.5，回答附带受控图片地址与可追溯来源。重排后续由 `services/retrieval.py` 编排，尚未实现。

目标为全本地通用多模态 RAG，模型位置、端口、预算和启动命令见 [模型部署指南](models.md)。原 `8000` 是共享环境记录，不自动覆盖。MinerU `8104` 是本机模型推理端点，业务仍通过 CLI 解析 PDF。

Embedding 与索引未验证时使用 BM25，重排默认关闭。没有图片能力验证时明确显示能力未就绪；文本 RAG 和 BM25 可作为分阶段联调路径，不能将其标为多模态功能全部完成。

Temporal 和 Neo4j 不参与当前链路。部署复用现有服务器进程，不以 Docker 或新增数据库为前提。

## 基础数据与接口边界

文档 ID 是稳定身份；解析任务 ID 标识一个解析版本，自动重试沿用该任务，人工重试新建任务并递增版本。documents.latest_version 表示最新任务，current_searchable_version 独立指向仍可检索的成功版本；新版本失败不影响旧版本查询。任务、outbox、会话、消息和图片资源的 SQL 约束见 [数据库设计](database.md)。

批量上传采用请求级限制校验后逐文件事务，返回有序逐项结果；问答和人工重试使用幂等键。图片资源只暴露内部 UUID URL，不暴露文件路径。接口形状、错误、状态、重试、分页和预算见 [API 契约](api.md)。

应用启动只建立惰性数据库连接池，不自动建表或迁移。MySQL readiness 仅验证连接及迁移版本，其他外部组件仍需专门联调。基础配置及限制见 [configuration.md](configuration.md)。
