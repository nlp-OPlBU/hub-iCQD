# 系统架构规划

当前仅为目录与设计，尚未实现服务逻辑。

```text
浏览器（Vue）
  └─ Nginx
      ├─ 前端构建静态文件
      └─ /api/ → FastAPI（规划 127.0.0.1:8001）
                   ├─ 共享文件存储：原始 PDF、用户图片与解析产物
                   ├─ MySQL：文档、任务、会话、消息、outbox
                   ├─ Elasticsearch：分块、图片说明、来源与可选向量
                   ├─ Redis：缓存与辅助协调
                   └─ vLLM（现有 8000）：文字及经验证的视觉问答

MySQL outbox → 独立投递器 → Kafka → PDF Worker
                                         ├─ MinerU CLI → ZIP → Markdown / JSON / 图片
                                         ├─ 分块 → ES（可选独立 Embedding 后写入向量）
                                         └─ 更新 MySQL 中的处理状态
```

上传过程在同一个 MySQL 事务中写入文档与 outbox 事件，独立投递器向 Kafka 发布。PDF 消费者按任务 ID 和版本幂等处理；文件保存、索引和状态更新均完成后才标记 `completed`。失败记录、重试及 offset 处理需要在实现时保证可恢复。

前端轮询 MySQL 驱动的状态接口；Redis 仅作为可失效缓存。问答先限定可检索文档，再检索相关文本和图片；用户原图以及选取的文档图片通过视觉输入传给模型，回答附带受控图片地址与可追溯来源。

未配置 Embedding 时使用 BM25。没有图片能力验证时明确显示能力未就绪；文本 RAG 和 BM25 可作为分阶段联调路径，不能将其标为多模态功能全部完成。

Temporal 和 Neo4j 不参与当前链路。部署复用现有服务器进程，不以 Docker 或新增数据库为前提。
