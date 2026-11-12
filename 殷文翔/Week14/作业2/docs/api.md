# HTTP API 契约（v1）

已实现 **健康检查、统一错误结构、请求 ID、PDF 批量上传、文档列表/详情、任务状态查询、人工重试**。图片资源、会话与问答仍为开发契约，尚未注册路由。Pydantic 定义在 `backend/app/schemas/`，前端类型在 `frontend/src/types/`。`/api/docs`、`/api/redoc`、`/api/openapi.json` 仅展示实际已实现接口；outbox 投递器、Kafka 生产/消费和可恢复解析 Worker 已实现；上传后任务会由独立进程推进，真实 MinerU/ES 联调仍待服务器验证。

## 1. 通用约定

- 前缀 `/api`，JSON UTF-8；上传使用 `multipart/form-data`。UUID v4 采用小写带连字符字符串；版本为从 1 开始的整数。API 时间为 UTC RFC 3339（`Z`）；MySQL DATETIME(6) 存 UTC，不随本机时区转换。
- 服务生成 `X-Request-ID`；错误体及批量结果包含相同 `request_id`。不信任客户端传入的日志关联 ID。
- 列表统一 `?page=1&page_size=20`，page ≥ 1、page_size 1～100。返回 `{ "items": [], "page": 1, "page_size": 20, "total": 0 }`；超出末页返回空 items，非法参数 422。文档/会话按 `created_at DESC, id DESC`，消息按 `sequence ASC`；分页和 total 在一次数据库读取事务中取快照。并发插入时跨请求页可能变化，首版不承诺游标快照。
- 无全局 success 包装：成功直接返回具体对象；错误如下。`details` 仅含字段名、允许值等可公开信息，不回显请求正文、密码、SQL、模型报错原文或服务器路径。
- 本阶段没有鉴权和多租户；只能用于可信本机/受控网络。接入用户体系前，图片访问和会话范围必须增加所有者校验。

```json
{
  "error": {
    "code": "VERSION_CONFLICT",
    "message": "文档已有更新的解析版本，请刷新后重试",
    "retryable": false,
    "details": { "latest_version": 3 }
  },
  "request_id": "65d4e67a-a889-41a2-afef-48a341b063dc"
}
```

| HTTP | code 示例 | 行为 |
| --- | --- | --- |
| 404 | `HTTP_404`、`DOCUMENT_NOT_FOUND`、`TASK_NOT_FOUND`、`IMAGE_NOT_FOUND` | 目标不存在 |
| 409 | `VERSION_CONFLICT`、`TASK_NOT_RETRYABLE`、`IDEMPOTENCY_CONFLICT`、`REQUEST_IN_PROGRESS` | 刷新状态或使用正确幂等键；不要盲目重试 |
| 413 | `REQUEST_TOO_LARGE`、`FILE_TOO_LARGE`、`IMAGE_BUDGET_EXCEEDED` | 减少文件或图片 |
| 415 | `INVALID_FILE_TYPE` | PDF/图片签名与格式校验失败 |
| 422 | `VALIDATION_ERROR`、`FILE_COUNT_EXCEEDED`、`INVALID_DOCUMENT_SCOPE` | 参数或业务输入非法 |
| 503 | `DATABASE_NOT_READY`、`SCHEMA_NOT_READY`、`STORAGE_UNAVAILABLE`、`MODEL_UNAVAILABLE`、`VISION_NOT_READY` | 状态未就绪，错误说明是否允许稍后重试 |
| 503 | `UPLOAD_COMMIT_UNCERTAIN` | 提交结果不明，保留文件和补偿记录；retryable=false，先查询列表并核对，禁止自动重传 |
| 504 | `MODEL_TIMEOUT` | 模型请求超时，失败消息需先可靠保存 |
| 500 | `INTERNAL_ERROR` | 对外通用错误，凭 request_id 排查 |

`retryable` 表示当前操作是否可能通过稍后重新尝试成功，不代表已经自动重试，也不等于有权新建解析版本。已有 HTTP 异常统一为 `HTTP_<status>`，后续业务实现应显式使用上表的业务 code。

## 2. 文档 ID、任务 ID、版本和状态

| 字段 | 含义 |
| --- | --- |
| `document.id` | 一次上传生成的稳定文档 ID。文件原名仅为元数据，相同文件再次上传也生成新文档 |
| `task.id` | 一次逻辑解析任务的 UUID；同一任务的自动重试及 Kafka 重投保持不变 |
| `task.version` | 该文档的解析版本；首次为 1，人工重试创建新的任务 ID 及 `latest_version + 1` |
| `document.latest_version` | 最新创建版本，对应唯一的 `(document_id, version)` 任务；前端主状态来自该任务 |
| `document.current_searchable_version` | 当前成功并可检索的版本，首次未完成时为 null；与 latest_version 可不同 |

例如文档 D 的任务 T1/v1 已完成：latest=1、searchable=1；重试/后续重新解析产生 T2/v2：latest=2、searchable 仍为 1。只有 v2 产物持久化、索引可用后，事务内把 T2 标成 completed 并切换 searchable=2。v2 失败仍可用 v1。首版人工重试仅开放 failed 任务；已完成文档主动重新解析接口暂不开放。

检索必须读取 MySQL 当前指针，以 **(document_id, current_searchable_version)** 成对过滤 ES，并确认指向的任务 completed；禁止分别用文档集合和版本集合做交叉组合。人工重试时不删除仍可检索的旧版本；切换后旧索引可异步清理，已引用的图片和来源仍保留。ES 分块 ID 必须包含文档 ID、版本和块序号。

SQL 外键保证指针属于同一文档的真实任务，不能单靠外键保证该任务 completed。后续 repository 的事务条件更新必须同时校验任务状态、租约令牌、当前 latest_version；过期 Worker 和旧版本不能覆盖新结果。首次上传须在一个事务内插入文档、v1 任务、outbox；事务提交前不得返回 accepted。

任务状态 `pending → processing → completed`；失败可从 pending/processing 进入 failed。可重试错误在预算内回到 pending 并记录 `next_attempt_at`；终态 failed 后由人工新建版本，不复活旧任务。阶段 `queued / parsing / persisting / indexing / done / failed`。attempts 是实际开始执行次数，默认最多 `1 + TASK_MAX_RETRIES = 4` 次；Kafka 重复投递本身不计执行次数。

```json
{
  "id": "8a450cdf-4e55-40d2-af6f-ac402366c3eb",
  "original_filename": "教材.pdf",
  "size_bytes": 12000,
  "latest_version": 1,
  "current_searchable_version": null,
  "latest_task": {
    "id": "30952743-45f2-4d9c-9c09-80fc90f85b20",
    "document_id": "8a450cdf-4e55-40d2-af6f-ac402366c3eb",
    "version": 1,
    "status": "pending",
    "stage": "queued",
    "attempts": 0,
    "max_retries": 3,
    "error": null,
    "next_attempt_at": null,
    "created_at": "2026-11-11T08:00:00Z",
    "updated_at": "2026-11-11T08:00:00Z"
  },
  "created_at": "2026-11-11T08:00:00Z",
  "updated_at": "2026-11-11T08:00:00Z"
}
```

## 3. 批量上传与查询（已实现，人工重试除外）

`POST /api/documents/batch`：multipart 中重复的 `files` 字段，保持提交顺序。默认最多 10 个 PDF，每个 50 MiB，整个 HTTP 请求最多 200 MiB（含 multipart 开销）。不能只信 Content-Length；读取时也必须累计限流，先将整个请求暂存并校验请求总上限，再开始逐文件提交，避免请求级 413 时已有文件悄悄入库。临时文件在失败后清理。

整个批次可解析且未触发请求级限制时统一 **HTTP 200**，逐文件返回 `202 accepted` 或具体错误；即使全部逐文件校验失败也为 200，客户端必须遍历 results，不得把 HTTP 200 当全部成功。格式损坏的 multipart 为 400；总量超限 413；文件数量超限 422；数据库在开始处理前不可用为 503，这些只返回通用错误体，没有逐文件结果。

每个有效文件独立落盘并独立提交 MySQL 事务；其中一个失败不回滚其他文件。事务失败清理该文件及孤立暂存文件；已提交文件在返回前不可删除。中途 DB/存储故障对应项返回 503，后续无法处理项也逐一返回拒绝结果。文件名不作唯一键，以从 0 开始的 index 区分同名文件。

实际校验包括：文件名去除客户端路径后不能为空、最多 255 字符、不含控制字符（422 INVALID_FILENAME）；扩展名 .pdf（大小写不敏感），MIME 为 application/pdf 或 application/octet-stream；开头必须是 `%PDF-1.0`～`%PDF-1.7` 或 `%PDF-2.0` 并跟随空白，末尾必须有 `%%EOF`（允许末尾空白/零字节）。类型或标记不符为 415 INVALID_FILE_TYPE；空文件 422 EMPTY_FILE，实际大小超限 413 FILE_TOO_LARGE。此处仅做容器标记检查，不验证 PDF 对象、交叉引用、加密或可解析性，完整解析留给 Worker；上传请求不调用 MinerU，也不直接投递 Kafka。

文件写入 `DATA_DIR/uploads/<document_id>.pdf`，保存实际字节数和 SHA256；原名仅作元数据。文件落盘前先持久化内部补偿记录。提交前写入失败、flush 失败会回滚文档/任务/outbox 并清理原文件；删除失败保留补偿记录。COMMIT 异常可能是提交成功但确认丢失，使用新数据库会话确认到同一文档/任务后仍返回 accepted；无法确认则逐项返回 503 UPLOAD_COMMIT_UNCERTAIN（details.compensation_required=true），保留文件，待停止上传后核对主库补偿，操作见 [数据库文档](database.md#上传文件补偿)。不会仅因一次查询未找到记录就删除提交结果不明的文件。

```json
{
  "request_id": "65d4e67a-a889-41a2-afef-48a341b063dc",
  "accepted": 0,
  "rejected": 2,
  "results": [
    { "index": 0, "filename": "a.txt", "outcome": "rejected", "status_code": 415,
      "error": { "code": "INVALID_FILE_TYPE", "message": "仅允许 PDF", "retryable": false, "details": {} } },
    { "index": 1, "filename": "large.pdf", "outcome": "rejected", "status_code": 413,
      "error": { "code": "FILE_TOO_LARGE", "message": "文件超过大小限制", "retryable": false, "details": { "max_bytes": 52428800 } } }
  ]
}
```

成功项形状为 `{index, filename, outcome: "accepted", status_code: 202, document: DocumentRead}`（document 见上一节完整例子）。失败项不返回有效 document_id/task_id。首版批量上传不承诺整批幂等；网络中断后先查询已有文档再决定补传，前端不自动重放整个批次。

| 方法与路径 | 响应 |
| --- | --- |
| `GET /api/documents?page=1&page_size=20&status=processing` | `Page[DocumentRead]`，status 可选、按最新任务过滤；检索可用性独立由指针决定 |
| `GET /api/documents/{document_id}` | `DocumentRead`；不存在 404 |
| `GET /api/tasks/{task_id}` | `TaskRead`（即 latest_task 同型）；允许查询历史任务 |
| `POST /api/documents/{document_id}/retry`（已实现） | 202，返回新任务 `TaskRead`；必须带 UUID `Idempotency-Key` |

重试必须带 `Idempotency-Key: <UUID>`，JSON `{ "expected_latest_version": 1 }`。当前实现事务锁定文档行：相同 key 和原请求返回原任务（不再次递增）；相同 key 不同原请求返回 409 IDEMPOTENCY_CONFLICT；不同 key 但版本过期返回 VERSION_CONFLICT；只有最新任务 failed 才可创建新版本，其他状态返回 TASK_NOT_RETRYABLE。文档 latest_version、新任务及 outbox 同事务提交；已终止旧版本与其错误记录保留，旧 Worker 的完成/失败更新会因版本条件失败而丢弃。

## 4. outbox、自动重试与租约

首版事件 `pdf.parse.requested` 与 `pdf.parse.failed`。outbox 具有独立 ID 和发送状态 `pending / publishing / published / failed`，与解析状态不同；`(task_id, event_type)` 唯一。消息只含如下元数据，不含 PDF、图片 Base64、绝对路径或模型提示。

```json
{
  "schema_version": 1,
  "event_id": "57bfb163-b7f3-4f85-9241-53646457ddc5",
  "document_id": "8a450cdf-4e55-40d2-af6f-ac402366c3eb",
  "task_id": "30952743-45f2-4d9c-9c09-80fc90f85b20",
  "version": 1,
  "storage_key": "uploads/8a450cdf-4e55-40d2-af6f-ac402366c3eb.pdf"
}
```

失败 Topic 仍用此元数据，错误摘要在 MySQL 查询。达到解析重试上限后，failed 状态和失败 outbox 一起提交，再确认消费 offset。Kafka 确认后才把 outbox 标记 published；发送成功但标记失败允许重发，消费者依据 task_id/version 幂等。

自动重试沿用原 task_id/version，以指数退避 `min(base * 2^(attempts-1), max)`（默认 5～300 秒）调度。当前 Worker 在失败事务中把任务置回 pending、记录错误与 next_attempt_at，并重置既有解析 outbox 的可用时间，不插入第二个同类型事件；投递器等 MySQL 到期后再发送。Worker 按 DB 到期时间和租约判断能否领取，提前重投不得提前执行。投递器同时恢复过期的 processing 租约。outbox 耗尽重试后持久化 failed、保留可运维恢复信息，并将尚 pending 的对应任务标记 failed，同时写入失败 Topic 事件，禁止静默丢弃。

任务租约默认 120 秒，每 30 秒续约，必须大于两倍心跳间隔；每次领取产生新的 lease_token，所有心跳、阶段及结果更新携带令牌条件和文档 latest_version 条件。租约过期后由投递器恢复，旧令牌不能提交。PDF 解析 1800 秒超时不等于租约时长；Kafka poll 与处理分离，并配置合理 max poll interval。offset 只在结果、重试安排或终态失败及失败 outbox 已提交后确认；持久化失败时不提交并回退到该记录重试。

## 5. 图片资源（待实现）

- `POST /api/conversations/{id}/images`：multipart `files`，返回 201 `{ "items": [ImageRead] }`。用户图片必须绑定已存在会话；本接口采用全部成功或全部失败（暂存校验全部通过才事务保存，失败清理），与 PDF 批量逐项语义不同。
- `GET /api/images/{image_id}`：返回 ImageRead 元数据。
- `GET /api/images/{image_id}/content`：返回二进制 png/jpeg/webp，设置正确 Content-Type、`X-Content-Type-Options: nosniff` 和私有缓存策略；资源不存在返回 404。不允许通过 URL 查询参数传服务器路径；存储 key 仅服务器读取。
- 提取图片保存 document_id/version/page（页码从 1 开始），用户原图保存 conversation_id。两种归属由数据库 CHECK 区分。旧回答引用的旧版本图片保留，首版不提供物理删除 API。

ImageRead 字段：`id, kind, url, media_type, size_bytes, width, height, document_id, version, page`；用户图片最后三项为 null。url 只允许 `/api/images/<uuid>/content`。格式校验需解码图片，拒绝 SVG 等主动内容及解压像素炸弹；不信扩展名。

默认每次最多上传 4 张、每张 10 MiB、原图不超过 2000 万像素。视觉输入最大边 2048；单次上下文总图片最多 8 张、解码后的模型输入图片字节总量最多 20 MiB、图像 token 预算 4096。总上下文输入预算 12000，输出上限 2048，必须结合实际模型窗口和图像 token 估算验证。超预算显式报错或按文档化规则压缩，不得静默丢弃用户图片；用户原图持久化保留，模型可使用受控缩放副本。

## 6. 会话、消息与问答（待实现）

| 方法与路径 | 请求与响应 |
| --- | --- |
| `POST /api/conversations` | JSON `{ "title": "新会话" }`，title 1～200 字符；201 ConversationRead |
| `GET /api/conversations` | 标准分页 Page[ConversationRead] |
| `GET /api/conversations/{id}/messages` | 标准分页 Page[MessageRead]，sequence 升序 |
| `POST /api/conversations/{id}/messages` | 提交下面的 QuestionRequest；首版非流式，200 AnswerResponse |

```json
{
  "text": "这张图与文档中的流程有什么区别？",
  "image_ids": ["41a57759-821c-43ed-b628-34f27b9bcdd4"],
  "document_ids": ["8a450cdf-4e55-40d2-af6f-ac402366c3eb"]
}
```

- text 最多 20000 字符，去空白后与 image_ids 至少一项非空；image_ids 不允许重复、必须属于当前会话且满足动态预算。
- document_ids=null 或省略代表全部当前可检索文档；空数组拒绝为 422，避免意外扩大范围。显式范围含不存在或不可检索文档返回 INVALID_DOCUMENT_SCOPE，不悄悄移除过滤条件。
- 必须带 UUID `Idempotency-Key`，规范化问题及范围计算 request_hash；相同 key/相同请求返回持久化结果（进行中 409 REQUEST_IN_PROGRESS，失败返回已保存错误，不再次调用模型），相同 key/不同请求 409。用户主动重试失败问答使用新 key，生成新消息对。
- 在短事务中锁定会话 next_sequence，分配用户及助手消息两个连续序号并保存；模型请求在事务外执行，结束后更新助手结果。消息 `status=pending/completed/failed` 与解析任务状态独立；用户消息持久化后 completed，助手消息可能 pending 或 failed。相同会话并发问题按分配序号排序，不能按模型返回顺序排序。
- AnswerResponse 为 `{ "user_message": MessageRead, "assistant_message": MessageRead }`。MessageRead 包含 id、conversation_id、sequence、role、status、content、images、sources、error、created_at、updated_at；content 是 Markdown。SourceReference 包含 document_id、version、filename、page、chunk_id、image_ids，页码未知为 null，禁止编造。
- document_scope JSON 持久化本次实际检索的 document_id/version 快照；来源不随后续文档重试被改写。无检索证据时说明依据不足，sources=[]。
- 用户原图、检索到的文档图片和文字要共同传到视觉模型；仅传路径或文字说明不符合契约。`LLM_VISION_ENABLED=false` 且问题带图片时返回 503 VISION_NOT_READY。该开关只有真实图片输入验证后才可开启。
- 前端 Markdown 禁止不安全 HTML/URL，图片仅允许受控资源地址；服务端将图片转换为受控 data URL 等模型可访问形式。文档和检索片段是数据，不能作为系统指令或执行授权。

## 7. 已实现的健康检查

`GET /api/health` → 200 `{ "status": "ok", "service": "Multimodal RAG", "scope": "process" }`。不访问 MySQL/Kafka/ES/模型；配置可加载时即可检查进程。

`GET /api/health/ready` → 检查 MySQL SELECT 1 和 alembic_version=0001_initial；成功 200 且 scope=mysql_schema，不可连接/无版本表 503 DATABASE_NOT_READY，版本不符 503 SCHEMA_NOT_READY。它仅表示数据库基础就绪，不表示 Kafka、MinerU、ES、图片问答已联调成功。启动 API 不自动创建数据库、建表或执行迁移。
