# 多模态 RAG 系统 Kafka 消息与 Worker 设计说明书

**文档版本：** V1.0  
**设计范围：** Kafka 消息协议、Outbox 投递、Worker 消费、任务执行、租约管理、异常恢复及人工恢复机制  
**关联文档：** 需求规格说明书 V1.0、总体技术架构与技术选型 V1.1、核心数据结构设计 V1.0

---

## 1. 设计目标

Kafka 在本系统中负责 PDF 解析任务的异步通知和分发，不负责保存 PDF 文件，也不负责维护任务的最终业务状态。

系统采用以下原则：

1. SQLite 是解析任务和文档状态的权威数据来源。
2. Kafka 负责传递任务通知，不传递 PDF 文件或完整业务数据。
3. Worker 从 SQLite 获取完整任务信息。
4. Kafka 消息允许重复投递，Worker 必须具备幂等消费能力。
5. Worker 使用租约机制管理任务执行权。
6. Worker 崩溃后，根据 SQLite 中的任务状态和执行阶段恢复任务。
7. MinerU 提交结果不确定时，禁止盲目重复提交。
8. 第一版不增加任务管理前端，特殊任务通过本地 Python CLI 人工处理。

## 2. 整体架构

### 2.1 组件职责

| 组件 | 职责 |
|---|---|
| FastAPI | 接收 PDF 上传请求，创建文档和解析任务 |
| SQLite | 保存文档、解析版本、任务状态及 Outbox |
| Outbox Dispatcher | 将待投递任务发送到 Kafka |
| Kafka | 保存和传递解析任务通知 |
| PDF Worker | 消费消息、领取任务、执行 PDF 解析 |
| MinerU | 云端 PDF 解析 |
| Elasticsearch | 保存 Chunk、文本分词结果和向量 |
| 本地文件系统 | 保存 PDF、解析产物及图片 |

### 2.2 整体流程

```text
用户上传 PDF
     |
     v
FastAPI
     |
     v
SQLite 事务
  |              |
创建 Document    创建 Parse Task
  |              |
  +-------> 创建 Outbox
                  |
                  v
           Outbox Dispatcher
                  |
                  v
                Kafka
                  |
                  v
             PDF Worker
                  |
                  v
             SQLite 领取任务
                  |
                  v
             提交 Kafka Offset
                  |
                  v
             MinerU 云端解析
                  |
                  v
             下载解析结果
                  |
                  v
             处理解析产物
                  |
                  v
             BGE Embedding
                  |
                  v
             Elasticsearch
                  |
                  v
             SQLite 切换有效版本
                  |
                  v
               完成任务
```

Worker 与 FastAPI 在 Mac 本地运行，为独立进程；Kafka 和 Elasticsearch 部署在 Linux 服务器。

Kafka 消息只传递任务元数据，Worker 通过 SQLite 获取任务完整信息。

## 3. Kafka 消息协议

### 3.1 Topic

第一版使用一个 PDF 解析任务 Topic：

`rag.pdf.parse.requested`

| 配置 | 值 |
|---|---|
| Topic | rag.pdf.parse.requested |
| Partition | 2 |
| Consumer Group | rag-pdf-parser-group |
| 消息格式 | JSON |
| 消息保留时间 | 7 天 |
| Message Key | task_uid |

Partition 数量初始设置为 2，与最大解析并发数 2 相匹配，但 Partition 数量不等于 Worker 的实际并发保证。

### 3.2 消息结构

采用极简消息结构，只传递五个字段：

```json
{
  "event_uid": "evt_20261114_001",
  "event_type": "document.parse.requested",
  "schema_version": 1,
  "task_uid": "task_20261114_001",
  "created_at": "2026-11-14T10:00:00Z"
}
```

字段说明：

| 字段 | 类型 | 说明 |
|---|---|---|
| event_uid | string | 消息唯一标识 |
| event_type | string | 消息类型 |
| schema_version | integer | 消息结构版本 |
| task_uid | string | 解析任务 UID |
| created_at | string | 消息创建时间，UTC |

约束：

- `event_type` 固定为 `document.parse.requested`。
- `schema_version` 第一版固定为 1。
- `task_uid` 必须对应 SQLite 中已有解析任务。
- `event_uid` 来自 Outbox 记录。
- Kafka Message Key 使用 `task_uid`。
- Pydantic Schema 设置 `extra="forbid"`，拒绝未知字段。

消息不包含：

- PDF 文件内容。
- 本地文件路径。
- MinerU API Token。
- DeepSeek API Key。
- 完整文档信息。
- Embedding 向量。
- Elasticsearch 数据。

Worker 根据 `task_uid` 查询 SQLite 获取完整任务和文档信息。

### 3.3 消息兼容策略

第一版只支持 `schema_version=1`。

遇到未知消息版本或不符合 Schema 的消息：

1. 记录 ERROR 日志。
2. 记录异常消息的必要标识。
3. 不执行任务。
4. 提交对应 Kafka Offset，避免错误消息持续阻塞 Partition。

后续消息结构升级时，增加新的 Schema 版本，并保持消费者兼容性管理。

## 4. Outbox 投递机制

### 4.1 设计原因

SQLite 和 Kafka 是两个独立系统，不能通过普通数据库事务保证同时成功。

例如：

- SQLite 创建任务成功，但 Kafka 发送失败。
- Kafka 发送成功，但 SQLite 更新发送状态失败。

为避免任务通知丢失，采用 Transactional Outbox Pattern。

### 4.2 任务创建

FastAPI 在同一个 SQLite 事务中完成：

1. 创建 documents 记录。
2. 创建 document_versions 记录。
3. 创建 parse_tasks 记录。
4. 创建 task_outbox 记录。

事务成功后，Outbox Dispatcher 才能看到待投递消息。

### 4.3 Outbox 状态

Outbox 状态：

| 状态 | 含义 |
|---|---|
| pending | 等待投递 |
| publishing | 正在投递 |
| sent | Kafka 已确认接收 |

`sent` 表示 Kafka 已确认接收消息，不代表 PDF 解析完成。

### 4.4 投递流程

```text
查询 pending Outbox
       |
       v
检查 Parse Task 状态
       |
       v
检查 Document 状态
       |
       v
获取 publishing 租约
       |
       v
发送 Kafka 消息
       |
       v
收到 Kafka 成功确认？
   /          \
  是           否
  |            |
  v            v
更新 sent    记录错误
               |
               v
          设置下次重试时间
```

投递前必须检查：

- Parse Task 仍然有效。
- Document 没有处于 deleting。
- 任务没有处于 completed、failed、cancelled 等不应投递状态。

投递失败时复用原 Outbox 记录，不创建新记录。

Outbox 采用至少一次投递语义，因此允许重复消息，Worker 必须保证幂等。

Producer 目标配置：

- 使用 Kafka 消息确认机制。
- 目标为 `acks=all`。
- 在客户端版本支持的前提下启用幂等 Producer。
- 实际配置必须以项目采用的 Kafka Python 客户端版本为准，不直接照搬其他客户端的参数。

第一版建议使用 aiokafka，具体参数在开发阶段根据实际版本验证。

## 5. Kafka Consumer 与 Offset 策略

### 5.1 Offset 提交原则

**采用 SQLite 成功领取任务后立即提交 Kafka Offset 的策略。**

不等待 PDF 解析完成才提交 Offset。

执行顺序：

1. 消费 Kafka 消息。
2. 校验消息结构。
3. 根据 task_uid 查询 SQLite。
4. 使用 SQLite 事务尝试领取任务。
5. 领取成功并提交 SQLite 事务。
6. 提交 Kafka Offset。
7. 将任务交给异步执行器。

这样可以避免 PDF 云端解析持续数分钟甚至更长时间时，Kafka Offset 长期不提交。

### 5.2 Offset 提交规则

| 情况 | 是否提交 Offset |
|---|---|
| 消息格式正确，SQLite 领取成功 | 是 |
| 任务已完成 | 是 |
| 任务已失败或取消 | 是 |
| 任务已有有效租约 | 是 |
| SQLite 不可用 | 否 |
| SQLite 领取事务失败 | 否 |
| SQLite 中不存在对应任务 | 记录异常后提交 |
| 消息格式错误 | 记录 ERROR 后提交 |
| 不支持的消息版本 | 记录 ERROR 后提交 |

SQLite 故障时不能提交 Offset，否则可能丢失任务通知。

### 5.3 Partition 顺序约束

同一 Partition 内必须顺序完成 SQLite 领取和 Offset 提交。

不能任意并发处理消息后，先提交较大的 Offset，否则可能跳过前面尚未处理的消息。

建议：

- Consumer 顺序处理每个 Partition 的领取和 Offset 提交。
- 领取成功后，将任务交给独立 Scheduler。
- Scheduler 管理最多两个执行 Slot。
- 没有空闲 Slot 时，暂停相关 Partition 或限制拉取量。
- 暂停期间仍需维持 Kafka Consumer 的 poll 和心跳，避免超过 max poll interval。

PDF 任务执行并发与 Kafka 消息消费并发分开管理。

## 6. Worker 架构

### 6.1 部署方式

FastAPI 与 Worker 在同一台 Mac 上运行，使用独立 Python 进程。

Worker 内部使用 asyncio 管理任务执行。

第一版最大解析并发数为 2：

- 一个 Worker 进程。
- 两个异步执行 Slot。
- 每个 Slot 同时执行一个 PDF 解析任务。

### 6.2 模块划分

建议目录：

```text
app/
├── worker/
│   ├── main.py
│   ├── consumer.py
│   ├── dispatcher.py
│   ├── scheduler.py
│   ├── executor.py
│   ├── lease_manager.py
│   ├── recovery_manager.py
│   ├── state_machine.py
│   ├── admin/
│   │   ├── main.py
│   │   ├── commands/
│   │   │   ├── list_unknown.py
│   │   │   ├── show_task.py
│   │   │   ├── attach_batch.py
│   │   │   └── retry_submit.py
│   │   └── services/
│   │       └── recovery_service.py
│   ├── stages/
│   │   ├── submit.py
│   │   ├── poll.py
│   │   ├── download.py
│   │   ├── process_assets.py
│   │   ├── embedding.py
│   │   ├── indexing.py
│   │   └── finalize.py
│   ├── clients/
│   │   ├── mineru.py
│   │   └── elasticsearch.py
│   └── services/
│       ├── document_service.py
│       └── task_service.py
```

### 6.3 Worker 启动流程

```text
启动 Worker
    |
    v
初始化 SQLite
    |
    v
生成 worker_id
    |
    v
扫描过期租约
    |
    v
恢复可恢复任务
    |
    v
启动 Recovery Manager
    |
    v
启动 Kafka Consumer
    |
    v
开始领取和执行任务
```

Recovery Manager 初始每 30 秒扫描一次租约过期任务。

## 7. Worker 租约机制

### 7.1 设计目标

Worker 可能发生：

- 进程崩溃。
- 网络断开。
- MinerU 请求超时。
- Elasticsearch 写入超时。
- Worker 被强制终止。
- Worker 长时间暂停。

不能仅依赖 Kafka Offset 判断任务是否仍在执行。

因此采用 SQLite 租约机制。

### 7.2 租约字段

建议在 parse_tasks 中增加：

| 字段 | 类型 | 说明 |
|---|---|---|
| execution_stage | TEXT | 当前执行阶段 |
| worker_id | TEXT | 当前执行 Worker |
| lease_token | TEXT | 当前租约令牌 |
| heartbeat_at | TEXT | 最近心跳时间 |
| lease_expires_at | TEXT | 租约到期时间 |
| stage_started_at | TEXT | 当前阶段开始时间 |
| stage_retry_count | INTEGER | 当前阶段重试次数 |
| submission_started_at | TEXT | 最近一次 MinerU 提交开始时间 |
| submission_attempt_count | INTEGER | MinerU 实际提交尝试次数 |

其中，后五个阶段计时和提交统计字段属于 Worker 设计的扩展建议，尚需合入最终 SQLite SQL。

### 7.3 租约参数

| 参数 | 初始值 |
|---|---:|
| 租约有效期 | 60 秒 |
| 心跳间隔 | 20 秒 |
| 恢复扫描间隔 | 30 秒 |

Worker 每 20 秒更新一次心跳，将租约延长至当前时间之后 60 秒。

### 7.4 Lease Token

每次领取或接管任务时，生成新的随机 `lease_token`。

所有写操作必须验证：

- task_uid。
- worker_id。
- lease_token。
- 当前任务状态。

例如：

```sql
UPDATE parse_tasks
SET
    heartbeat_at = :heartbeat_at,
    lease_expires_at = :lease_expires_at
WHERE
    id = :task_id
    AND worker_id = :worker_id
    AND lease_token = :lease_token
    AND status = 'processing';
```

如果影响行数为 0，说明 Worker 已失去任务执行权，必须停止后续处理。

SQLite 任务领取使用 `BEGIN IMMEDIATE` 和条件 UPDATE 实现原子领取，不使用 PostgreSQL 风格的 SELECT FOR UPDATE。

### 7.5 租约失效处理

旧 Worker 即使恢复运行，也不能继续更新 SQLite 任务状态。

但 Lease Token 无法撤销已经发出的 Elasticsearch 请求。

因此：

- ES Chunk 使用确定性 ID。
- 新旧解析版本使用不同 document_version_id。
- ES 写入必须幂等。
- 写入前后检查租约。
- 删除流程必须重复清理可能残留的 ES 数据。

## 8. Worker 执行状态机

### 8.1 业务状态与执行阶段分离

`status` 表示业务状态：

- pending
- processing
- completed
- failed
- cancelled

`execution_stage` 表示执行阶段：

- queued
- claimed
- submitting
- submission_unknown
- polling
- downloading
- processing_assets
- embedding
- indexing
- finalizing
- completed
- failed
- cancelled

### 8.2 正常执行流程

```text
queued
  |
  v
claimed
  |
  v
submitting
  |
  v
polling
  |
  v
downloading
  |
  v
processing_assets
  |
  v
embedding
  |
  v
indexing
  |
  v
finalizing
  |
  v
completed
```

### 8.3 阶段恢复规则

| 阶段 | 恢复策略 |
|---|---|
| claimed | 重新领取并继续准备 |
| submitting | 租约过期后转 submission_unknown |
| submission_unknown | 等待人工核对 |
| polling | 复用已有 batch_id 继续轮询 |
| downloading | 检查文件后恢复下载 |
| processing_assets | 检查解析产物后恢复 |
| embedding | 检查向量处理结果后恢复 |
| indexing | 使用确定性 ID 幂等补写 ES |
| finalizing | 检查版本切换结果后恢复 |

进入新阶段时更新 `stage_started_at`，并重置 `stage_retry_count`。

终态任务清空租约字段，但保留最后执行阶段。

## 9. MinerU 提交机制

### 9.1 提交流程

Worker 调用 MinerU 云端 API，创建解析任务。

MinerU 返回 `batch_id` 后，Worker 必须立即将其写入：

`document_versions.mineru_batch_id`

之后进入 polling 阶段。

建议将 MinerU 的 `data_id` 设置为 `task_uid`，用于业务关联。

但 `data_id` 不能被视为幂等键，不能假设重复提交相同 data_id 会自动复用原任务。

### 9.2 提交结果不确定

这是整个 Worker 设计中需要特别保护的环节。

例如：

1. Worker 向 MinerU 发起创建任务请求。
2. MinerU 已经成功创建云端任务。
3. 返回结果时网络断开。
4. Worker 没有获得 batch_id。

此时无法判断云端是否已经创建任务。

第一版采用保守策略：

**不自动重新提交，进入 submission_unknown，等待人工核对。**

处理规则：

- `status=processing`。
- `execution_stage=submission_unknown`。
- 保存错误信息和提交时间。
- 清空当前租约。
- 不允许普通 Worker 自动领取该任务。
- 不允许 Recovery Manager 自动重新提交。

租约在 submitting 阶段过期时，也必须转入 submission_unknown。

这样可以避免云端已经创建任务、Worker 却重复创建任务。

## 10. 人工恢复 CLI

### 10.1 设计原则

第一版不增加 FastAPI 管理接口和前端任务管理页面。

通过本地 Python CLI 管理 submission_unknown 任务。

### 10.2 命令设计

```bash
# 查看所有提交结果不确定的任务
python -m app.worker.admin list-unknown

# 查看指定任务详情
python -m app.worker.admin show-task \
  --task-uid task_20261114_001

# 人工确认 MinerU 已创建任务，补录 batch_id
python -m app.worker.admin attach-batch \
  --task-uid task_20261114_001 \
  --batch-id batch_xxx

# 人工确认 MinerU 没有创建任务，允许重新提交
python -m app.worker.admin retry-submit \
  --task-uid task_20261114_001 \
  --confirmed-not-created
```

### 10.3 attach-batch

适用场景：人工确认 MinerU 已经创建任务，但本地没有保存 batch_id。

执行前显示任务信息，并要求用户确认。

同一个 SQLite 事务中：

1. 检查任务仍处于 submission_unknown。
2. 检查文档未处于 deleting。
3. 检查解析版本未取消。
4. 更新 document_versions.mineru_batch_id。
5. 将 parse_tasks.status 更新为 pending。
6. 将 execution_stage 更新为 polling。
7. 清空租约。
8. 将原 Outbox 重置为 pending。
9. 写入 operation_logs。

之后由 Worker 重新领取任务，从 polling 阶段继续。

### 10.4 retry-submit

适用场景：人工确认 MinerU 没有创建云端任务。

必须显式传入 `--confirmed-not-created`。

同一个 SQLite 事务中：

1. 检查任务仍处于 submission_unknown。
2. 检查 batch_id 为空。
3. 检查文档未处于 deleting。
4. 检查解析版本未取消。
5. 将 status 更新为 pending。
6. 将 execution_stage 更新为 claimed。
7. 清空租约。
8. 将原 Outbox 重置为 pending。
9. 写入 operation_logs。

之后 Worker 重新领取任务，执行 MinerU 提交。

CLI 不直接调用 MinerU。

### 10.5 CLI 事务安全

所有管理操作：

- 使用 `BEGIN IMMEDIATE`。
- 在事务内部重新读取和检查状态。
- 使用条件 UPDATE。
- 检查 affected_rows 是否等于 1。
- 状态已变化时回滚并提示重新核对。
- 状态更新、Outbox 重置、操作日志写入必须处于同一个事务。

Outbox 复用原记录和原 event_uid，不新建 Outbox 记录。

CLI 不记录 API Token、PDF 内容等敏感信息。

## 11. 重试策略

区分通信异常与业务解析失败。

| 异常类型 | 策略 |
|---|---|
| 网络连接异常 | 有限次数自动重试 |
| 请求超时 | 根据操作阶段决定是否重试 |
| MinerU 轮询临时失败 | 有限次数自动重试 |
| 解析文件下载失败 | 有限次数自动重试 |
| ES 临时写入失败 | 只重试失败项目 |
| MinerU 明确解析失败 | 标记失败，不自动重试 |
| PDF 损坏 | 标记失败，不自动重试 |
| MinerU 提交结果不确定 | submission_unknown，人工核对 |
| Worker 租约失效 | 立即停止执行 |

阶段重试初始建议：

- 每阶段最多 3 次。
- 退避时间 2 秒、4 秒、8 秒。
- 阶段之间独立计数。

具体参数在实际运行中调整。

已经开始 SSE 流式回答的 LLM 请求不属于本 Worker 流程；问答流式输出中途失败时不能简单从头重试并追加，以免产生重复内容。

## 12. Elasticsearch 写入与任务完成

Worker 完成 Embedding 后：

1. 检查当前租约。
2. 检查文档未处于 deleting。
3. 使用 AsyncElasticsearch 写入 Chunk。
4. 使用确定性 Chunk ID。
5. 逐条检查 Bulk 结果。
6. 失败项目有限重试。
7. 全部写入后 Refresh。
8. 按 document_version_id 核对 Chunk 数量。
9. 进入 finalizing。

finalizing 阶段在 SQLite 事务中再次检查：

- 当前任务租约仍有效。
- 文档未处于 deleting。
- 新版本 ES 数据已完成验证。

然后切换 `documents.current_version_id`，更新文档、版本和任务状态。

切换成功后清理旧版本文件和 ES 数据。

如果清理失败，保留可恢复状态，由后续恢复流程继续清理。

## 13. 文档删除与 Worker 协调

删除文档时：

1. SQLite 事务标记 document 为 deleting。
2. 取消关联未完成任务。
3. Worker 在阶段操作和 ES 写入前检查文档状态。
4. 等待正在执行的 Worker 停止或租约失效。
5. 清理 ES 中该 document_id 的所有版本数据。
6. 清理本地 PDF 和解析产物。
7. 核对外部数据已清理。
8. 最终删除 SQLite 文档记录，依靠外键级联删除关联数据。

由于 SQLite 和 Elasticsearch 不具备跨系统事务，删除流程必须支持幂等重试。

## 14. 关键设计约束

1. SQLite 是任务状态权威来源，Kafka 只是任务通知。
2. Kafka 至少一次投递，允许重复消息。
3. Worker 必须幂等。
4. Kafka Offset 在 SQLite 成功领取后提交。
5. 同一 Partition 的领取和 Offset 提交保持顺序。
6. Worker 最大解析并发数为 2。
7. 每个任务通过 lease_token 管理执行权。
8. MinerU 提交不确定时禁止自动重提。
9. Elasticsearch 写入使用确定性 ID 和版本隔离。
10. 文档删除必须防止 Worker 在删除后继续写入。
11. submission_unknown 通过本地 CLI 人工处理。
12. 所有恢复和管理操作必须可审计。

## 15. 第一版不实现的功能

- 前端任务管理页面。
- 用户手动取消解析任务。
- 自动判断 MinerU 不确定提交是否已创建任务。
- MinerU 公网 Callback 服务。
- 多 Worker 主机分布式调度。
- Redis 分布式锁。
- 自动无限重试。
- 复杂的任务优先级调度。

---

**文档状态：** 设计决策已确认，具体实现参数仍需开发验证。