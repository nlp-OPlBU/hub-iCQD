# 06_API接口设计_V1.0

> **项目：** 多模态 RAG 知识库\
> **文档编号：** 06\
> **版本：** V1.0\
> **状态：** API
> 架构决策已确认；具体字段、部分边界行为待接口详细设计与实现阶段补齐\
> **决策范围：** ARCH-API-01 ～ ARCH-API-25\
> **整理日期：** 2026-11-14

------------------------------------------------------------------------

## 1. 文档目的

本文档汇总多模态 RAG 项目第一版 API
层的已确认设计，作为前后端协作和后续接口实现的依据。

本文重点规定：

-   API 风格、版本前缀、请求与响应约定；
-   认证、Cookie 会话与 CSRF 防护；
-   PDF 文档上传、查询、删除和重新解析；
-   对话、消息、图片附件与多模态问答；
-   SSE 流式输出协议；
-   用户设置、系统健康检查、统计和操作日志；
-   幂等性、错误码和请求追踪。

本文区分"已确认决策"和"待补充细节"。示例用于说明协议形态，不代表所有字段均已完成最终
Schema 定义。未明确的字段约束应在 Pydantic Schema
设计时补齐，不应由前端或后端各自猜测。

## 2. 项目技术边界

### 2.1 已知组件边界

  ----------------------------------------------------------------------------------------------------
  层次                    组件                    职责
  ----------------------- ----------------------- ----------------------------------------------------
  前端                    React + TypeScript +    页面、交互、文件上传、SSE 消费
                          Vite                    

  UI                      Tailwind CSS +          样式与组件
                          shadcn/ui               

  API 服务                FastAPI + Pydantic      REST API、认证、业务编排、SSE

  业务数据库              SQLite                  文档、任务、版本、对话、消息、设置等关系型业务数据

  消息队列                Kafka                   异步解析任务传递

  检索引擎                Elasticsearch           全文检索、向量检索及融合检索

  解析服务                MinerU 云端 API         PDF 解析

  模型服务                DeepSeek API            问答生成

  Embedding               BGE 系列模型            文本向量生成

  文件存储                本地文件系统            原始 PDF、解析产物、图片及用户上传附件
  ----------------------------------------------------------------------------------------------------

Redis 第一版不是必需组件。API 不应把 Redis 作为业务正确性的前提。

### 2.2 重要业务约束

1.  第一版只接受 PDF 文件。
2.  单个 PDF 最大 100 MB；单次最多上传 10 个文件。
3.  上传成功后自动创建解析任务并进入 Kafka
    队列，不需要用户手动开始解析。
4.  SQLite 保存业务状态；Elasticsearch 是可重建的检索索引。
5.  文档只有在解析产物持久化、图片保存、Chunk/向量写入 Elasticsearch
    并完成必要校验后，才可切换为可检索状态。
6.  文档解析、重新解析和删除需要遵守文档状态及版本隔离规则。
7.  删除流程采用 `deleting` 标记、取消关联任务、清理 Elasticsearch
    和本地文件，最后删除 SQLite 文档记录；失败时保留可恢复状态。
8.  Elasticsearch 检索采用已确认的原生 RRF
    单次检索请求；请求超时或整体失败时有限重试一次，仍失败则终止本次 RAG
    问答，不绕过检索直接调用
    LLM。单路检索正常返回空结果不算异常；超时或分片失败不能将部分结果当作完整结果。
9.  MinerU 第一版采用 VLM 云端 API；Worker
    负责调用、轮询、下载和后处理。
10. 历史对话中的来源引用快照应与当前知识库资源生命周期区分；删除文档不应抹去历史消息中已经保存的引用文字快照。

## 3. 全局 API 规范

### 3.1 API 风格与版本

-   风格：RESTful。
-   统一前缀：`/api/v1`。
-   后端：FastAPI + Pydantic。
-   普通请求和响应：JSON。
-   文件上传：`multipart/form-data`。
-   流式问答：POST + SSE。
-   文件资源读取：由专用资源接口返回文件内容，不使用普通 JSON 响应包装。

### 3.2 HTTP 方法约定

  方法     语义                 常见用途
  -------- -------------------- ----------------------------------------
  GET      读取资源             列表、详情、状态、统计
  POST     创建资源或触发动作   创建对话、发送消息、重新解析、健康检查
  PATCH    局部更新             修改对话标题、设置
  PUT      完整替换或幂等更新   消息评价
  DELETE   删除资源             删除文档、对话、消息

### 3.3 统一 JSON 响应格式

普通 JSON API 统一使用以下结构。

**成功：**

``` json
{
  "success": true,
  "data": {
    "id": "resource_001"
  },
  "error": null
}
```

**失败：**

``` json
{
  "success": false,
  "data": null,
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "文档不存在",
    "details": null
  }
}
```

字段定义：

  字段              类型                    说明
  ----------------- ----------------------- ------------------------
  `success`         boolean                 业务请求是否成功
  `data`            object / array / null   成功时的业务数据
  `error`           object / null           失败时的错误信息
  `error.code`      string                  稳定的机器可读错误码
  `error.message`   string                  面向用户的中文提示
  `error.details`   object / array / null   字段校验或其他补充信息

约束：

-   HTTP 状态码必须表达真实请求结果，不能所有业务错误都返回 HTTP 200。
-   SSE 和文件内容响应不使用该 JSON 包装。
-   前端根据 `error.code` 分支处理，不应依赖中文 `message`
    判断业务逻辑。
-   不向客户端返回 Python
    堆栈、数据库内部异常、文件系统绝对路径、内部凭证或其他敏感信息。

### 3.4 HTTP 状态码约定

    状态码 含义                                 示例
  -------- ------------------------------------ ------------------------------
       200 请求成功                             查询、更新、同步删除成功
       201 资源创建成功                         创建对话
       202 已接受、后台处理中                   若某接口最终采用异步受理语义
       400 请求格式或业务参数错误               非法请求
       401 未认证或认证失败                     密码错误、认证过期
       403 已认证但无权执行，或 CSRF 校验失败   CSRF Token 无效
       404 资源不存在                           文档不存在
       409 资源状态冲突或幂等键冲突             文档正在解析
       413 请求或文件过大                       PDF 超过限制
       422 参数或字段校验失败                   文件类型不支持
       500 服务内部错误                         未预期异常
       502 上游服务调用失败                     LLM 请求失败
       503 依赖服务不可用                       Elasticsearch 不可用

具体接口应在详细 OpenAPI Schema 中明确成功状态码和可能的错误状态码。

### 3.5 分页规范

普通列表接口统一采用页码分页。

请求参数：

  参数          类型        默认值 约束
  ------------- --------- -------- -----------
  `page`        integer          1 从 1 开始
  `page_size`   integer         20 最大 100

响应数据建议采用：

``` json
{
  "items": [],
  "pagination": {
    "page": 1,
    "page_size": 20,
    "total": 100,
    "total_pages": 5
  }
}
```

适用：文档列表、解析任务历史、对话列表、消息列表、操作日志列表。

### 3.6 时间、标识符与排序约定

以下细节需要在 OpenAPI/Pydantic 详细设计中统一落实：

-   时间字段统一采用带时区的 ISO 8601 格式，建议服务端统一输出 UTC。
-   ID 使用不透明字符串，前端不解析其内部结构。
-   排序字段必须使用服务端白名单，禁止将客户端输入直接拼接为 SQL 或
    Elasticsearch 字段。
-   排序方向仅允许 `asc` 或 `desc`。
-   对分页列表应增加稳定的次级排序键，避免同一排序值造成跨页重复或遗漏。

## 4. 认证与安全

### 4.1 认证方式

采用"访问密码 + HttpOnly Cookie"：

-   单用户系统，不提供注册、账号体系或多用户管理。
-   访问密码由后端配置文件维护，不通过用户设置 API 暴露。
-   登录成功后由服务端签发无状态签名 Cookie。
-   Cookie 有效期：12 小时。
-   Cookie 设置 `HttpOnly`；正式 HTTPS 环境设置 `Secure`；配置合适的
    `SameSite`。
-   前端不在 `localStorage` 等位置保存长期认证 Token。

### 4.2 无状态 Cookie 会话

-   Cookie 由服务端签名并校验有效期。
-   不新增 SQLite Session 表。
-   服务重启后，未过期且签名有效的 Cookie 仍可使用。
-   退出登录通过清除 Cookie 实现。

**语义限制：** 纯无状态 Cookie 的清除只能让当前浏览器停止携带该
Cookie，不能在服务端立即撤销已经被复制的有效
Cookie。若未来要求服务端即时撤销单个会话，需要重新评估引入撤销机制或服务端会话存储；这不属于当前已确认的第一版方案。

### 4.3 认证接口

  方法   路径                    说明
  ------ ----------------------- ---------------------------------
  GET    `/api/v1/auth/status`   查询认证状态及获取 CSRF Token
  POST   `/api/v1/auth/login`    提交访问密码，成功后签发 Cookie
  POST   `/api/v1/auth/logout`   清除认证 Cookie

登录请求示例：

``` json
{
  "password": "用户输入的访问密码"
}
```

登录失败返回
`401`。登录接口必须实施失败次数限制/速率限制，具体阈值、窗口和锁定策略待安全配置阶段确定。

### 4.4 CSRF 防护

采用服务端验证的 Synchronizer Token 思路，并与无状态签名会话兼容：

1.  登录成功后生成与当前签名会话绑定的 CSRF Token。
2.  前端通过 `GET /api/v1/auth/status` 获取 Token。
3.  所有状态变更请求通过请求头携带：

``` http
X-CSRF-Token: <csrf_token>
```

4.  服务端验证 Token 的签名、有效期及其与当前会话的绑定关系。
5.  GET、HEAD 等安全读取请求不要求 CSRF Token。
6.  认证 Cookie 始终保持 HttpOnly。

CSRF Token
的生成、签名格式、轮换时机和常量时间比较等实现细节在安全实现阶段明确。不得为了
CSRF 而把认证 Cookie 改为 JavaScript 可读。

### 4.5 安全通用要求

-   对所有需要认证的业务 API 执行认证校验。
-   校验上传文件内容，不仅依赖扩展名和客户端 MIME。
-   对路径参数、分页参数、排序字段和筛选值进行严格校验。
-   防止任意文件路径访问；资源 ID 必须通过数据库记录解析到受控存储路径。
-   对外错误信息脱敏。
-   日志不得记录访问密码、API Key、MinerU Token、完整认证 Cookie 或 CSRF
    Token。
-   对跨域策略采用最小允许来源；如前后端跨源部署，明确允许的
    Origin、凭证和预检策略。

## 5. 文档管理 API

### 5.1 上传 PDF

**接口**

``` http
POST /api/v1/documents/upload
Content-Type: multipart/form-data
```

字段：

  字段      类型       说明
  --------- ---------- ----------------------------------
  `files`   file\[\]   PDF 文件，可重复提交，最多 10 个

限制：

-   仅 PDF。
-   单文件最大 100 MB。
-   单次最多 10 个文件。
-   前端通过 Axios/XHR 上传进度事件展示上传进度。
-   每个文件独立处理，允许部分成功。

响应示例：

``` json
{
  "success": true,
  "data": {
    "results": [
      {
        "filename": "document_a.pdf",
        "success": true,
        "document_id": "doc_001",
        "status": "pending",
        "error": null
      },
      {
        "filename": "document_b.pdf",
        "success": false,
        "document_id": null,
        "status": null,
        "error": {
          "code": "FILE_TOO_LARGE",
          "message": "文件超过 100 MB"
        }
      }
    ],
    "summary": {
      "total": 2,
      "succeeded": 1,
      "failed": 1
    }
  },
  "error": null
}
```

业务规则：

-   批次请求被成功处理，不代表每个文件都成功。
-   每个文件单独校验、保存和返回结果。
-   单个文件上传成功后自动创建文档记录、解析版本、解析任务及必要的可靠投递记录，并进入
    Kafka 异步解析流程。
-   用户无需点击"开始解析"。
-   Kafka 投递与 SQLite
    状态更新必须采用项目已确定的可靠性设计，不能把"已写入
    SQLite"误报为"Kafka 已可靠消费"。

**待补充：** 多文件请求整体超过网关请求体限制时的 HTTP
行为；文件名冲突策略；PDF
加密/损坏文件的具体错误码；单文件保存失败后的清理策略。

### 5.2 文档列表

**接口**

``` http
GET /api/v1/documents
```

Query 参数：

  参数           说明           示例
  -------------- -------------- --------------
  `page`         页码           `1`
  `page_size`    每页数量       `20`
  `keyword`      文件名搜索     `机器学习`
  `status`       解析状态筛选   `completed`
  `sort_by`      排序字段       `created_at`
  `sort_order`   排序方向       `desc`

允许排序字段：

-   `created_at`
-   `updated_at`
-   `filename`
-   `file_size`

对外列表状态：

-   `pending`
-   `processing`
-   `completed`
-   `failed`

`deleting`
是内部删除过程状态，不作为普通列表筛选状态。列表采用统一分页响应结构。

### 5.3 文档详情

**接口**

``` http
GET /api/v1/documents/{document_id}
```

返回文档基本信息及当前有效解析版本信息。建议包含：

-   文档 ID、文件名、文件大小、上传时间；
-   当前解析状态；
-   当前有效解析版本；
-   解析开始时间、完成时间；
-   解析失败原因（如有）；
-   Chunk 数量；
-   PDF 页数（如解析结果提供）。

不得返回本地文件系统路径、MinerU 凭证或其他内部敏感字段。

### 5.4 文档解析任务历史

**接口**

``` http
GET /api/v1/documents/{document_id}/tasks
```

-   使用统一页码分页。
-   返回任务状态、开始时间、完成时间、错误信息等业务字段。
-   不暴露 Worker 内部租约令牌、Kafka Offset 等实现细节。

### 5.5 删除单个文档

**接口**

``` http
DELETE /api/v1/documents/{document_id}
```

采用同步执行，成功后返回。业务步骤：

1.  将文档标记为 `deleting`，并取消关联的未完成解析任务。
2.  清理 Elasticsearch 中该文档及相关版本的数据。
3.  清理本地原始文件和解析产物。
4.  删除 SQLite 文档记录，由外键级联清理关联业务记录。
5.  全部完成后返回成功。

失败处理：

-   不得在外部资源尚未清理成功时直接删除 SQLite 主记录。
-   清理失败时保留 `deleting`
    状态及必要的恢复信息，交由后台恢复流程处理。
-   Worker 在写入 Elasticsearch
    前必须重新检查文档/版本/任务状态，防止删除后迟到的 Worker
    再次写入数据。
-   历史消息中已经保存的来源引用文字快照继续保留。

同步接口可能因外部清理耗时较长而遇到客户端/代理超时。请求断开不代表删除事务已回滚；前端后续应重新查询文档状态。

### 5.6 重新解析文档

**接口**

``` http
POST /api/v1/documents/{document_id}/reparse
```

业务规则：

-   创建新的解析版本和解析任务，并自动投递 Kafka。
-   不要求用户手动开始。
-   旧版本在新版本成功并完成切换前继续有效。
-   同一文档不允许同时存在多个未完成解析任务，避免并发版本冲突。
-   新版本失败时，旧有效版本继续可用。

### 5.7 清空知识库

**接口**

``` http
DELETE /api/v1/documents
```

-   清空全部文档及关联解析版本、任务和文件。
-   保留历史对话。
-   保留历史消息中的来源引用快照。
-   前端必须进行二次确认。
-   按同步删除语义执行清理；失败时保留可恢复状态。

**待补充：**
清空操作的批次事务边界、部分清理失败时的恢复进度、接口超时和重复调用语义。

## 6. 对话管理 API

### 6.1 创建对话

``` http
POST /api/v1/conversations
```

允许传入初始标题；未传标题时可在首轮提问后根据问题生成标题。标题生成策略、长度限制和是否异步生成待详细设计确认。

### 6.2 对话列表

``` http
GET /api/v1/conversations
```

支持：

-   `page`、`page_size` 分页；
-   标题关键词搜索；
-   置顶筛选；
-   按更新时间排序。

### 6.3 对话详情

``` http
GET /api/v1/conversations/{conversation_id}
```

返回对话基本信息。消息列表通过独立接口分页获取，避免详情接口返回过大的消息集合。

### 6.4 修改对话

``` http
PATCH /api/v1/conversations/{conversation_id}
```

用于修改标题和置顶状态。采用局部更新语义；具体请求字段由 Pydantic Schema
定义。

### 6.5 删除对话

``` http
DELETE /api/v1/conversations/{conversation_id}
```

-   物理删除对话。
-   级联删除关联消息及附件关联记录。
-   由业务层清理关联本地附件文件。
-   保留 `llm_call_records` 中的历史调用统计。

**待补充：**
删除对话时附件文件清理失败的恢复策略；调用记录与对话/消息之间的外键策略需与数据库设计保持一致。

## 7. 消息与问答 API

### 7.1 消息列表

``` http
GET /api/v1/conversations/{conversation_id}/messages
```

-   使用统一页码分页。
-   按对话内消息时间顺序返回。
-   返回消息内容、角色、状态、附件引用、来源快照及评价等允许前端展示的字段。
-   不返回内部模型凭证、检索内部调试数据或 Worker 私有字段。

### 7.2 发送消息与多模态提问

``` http
POST /api/v1/conversations/{conversation_id}/messages
Content-Type: multipart/form-data
Accept: text/event-stream
Idempotency-Key: <unique-key>
```

表单字段：

  字段        类型       说明
  ----------- ---------- ---------------------------------
  `content`   string     用户问题
  `images`    file\[\]   用户上传图片，可重复，最多 5 张

支持：

-   纯文本提问；
-   文本 + 用户上传图片；
-   多轮对话上下文；
-   RAG 检索到的 PDF 图片作为回答来源内容。

后端负责保存用户图片、创建用户消息及附件关联记录，并启动 RAG
问答。用户上传图片与 MinerU 从 PDF
提取的图片属于不同资源类型，存储生命周期和访问接口分离。

**注意：** 该端点同时接收 multipart 请求并返回 SSE 响应。前端使用
Fetch + ReadableStream 读取 SSE；普通浏览器 `EventSource` 不适合该
POST + multipart 请求模式。

### 7.3 编辑用户消息

``` http
PATCH /api/v1/messages/{message_id}
```

业务规则：

-   仅允许编辑用户消息。
-   更新当前用户消息内容。
-   删除该消息之后的所有消息。
-   清理被删除消息关联的附件文件和引用记录。
-   后续由用户重新发起问答。

编辑接口是否允许同时更换附件、如何上传替换附件，需在详细 Schema
中明确；当前已确认的是消息编辑会使后续消息失效。

### 7.4 重新生成 AI 回答

``` http
POST /api/v1/messages/{message_id}/retry
```

业务规则：

-   不新增用户消息。
-   重新执行当前用户问题的 RAG 流程。
-   覆盖原 AI 回答及其引用来源。
-   保留历史 LLM 调用统计。
-   不应将新旧两次生成的文本拼接成一条回答。

**待补充：** 重试端点的响应协议是否同样采用
SSE，以及重试时是否复用原消息的附件集合。建议与发送消息保持一致的流式协议，但此项需在接口详细设计中明确。

### 7.5 删除消息

``` http
DELETE /api/v1/messages/{message_id}
```

删除消息及其允许级联清理的数据，并清理相关附件文件。删除单条消息是否同时删除其后的消息、是否允许删除
AI
消息，需与消息顺序和对话一致性规则一并明确；当前已确认的级联删除规则明确适用于"编辑用户消息"。

### 7.6 消息评价

``` http
PUT /api/v1/messages/{message_id}/feedback
```

支持：

-   `like`
-   `dislike`
-   取消评价

同一消息不重复创建评价记录。建议采用幂等更新语义，具体取消评价的请求体表达方式待
Schema 阶段确定。

## 8. SSE 流式响应协议

### 8.1 传输约定

-   方法：POST。
-   媒体类型：`text/event-stream`。
-   字符编码：UTF-8。
-   前端：Fetch + ReadableStream。
-   每个 SSE 事件以空行结束。
-   不使用普通 JSON 响应包装。
-   Request ID 通过响应头 `X-Request-ID` 返回。

### 8.2 事件类型

  事件                 用途
  -------------------- -----------------------
  `message_start`      开始生成，返回消息 ID
  `content_delta`      返回一段回答文本
  `sources`            返回 PDF 来源引用
  `message_complete`   回答生成成功
  `error`              生成过程中发生错误

示例：

``` text
event: message_start
data: {"message_id":"msg_001"}

event: content_delta
data: {"delta":"根据文档内容，"}

event: content_delta
data: {"delta":"可以得出以下结论……"}

event: sources
data: {"items":[{"document_id":"doc_001","page_number":3}]}

event: message_complete
data: {"message_id":"msg_001","finish_reason":"stop"}
```

错误示例：

``` text
event: error
data: {"code":"LLM_REQUEST_FAILED","message":"模型服务暂时不可用"}
```

### 8.3 事件语义

1.  `message_start` 表示服务端已创建/确认本次回答对应的消息。
2.  `content_delta` 只承载新增文本片段，不重复发送此前内容。
3.  `sources` 承载本次回答引用的来源信息；来源字段的完整 Schema 待定。
4.  只有完整成功时才能发送 `message_complete`。
5.  生成中途失败时发送 `error`，不能先发送成功完成事件。
6.  流式输出已经产生部分内容后，不允许从头重试并把重复内容追加到原流。
7.  客户端断开后，后端应停止不再需要的生成工作，并妥善保存消息状态。
8.  如果客户端断开时服务端已完成持久化或生成，客户端应通过消息查询接口恢复最终状态。

### 8.4 断连与失败状态

需在实现中明确区分：

-   用户主动取消；
-   浏览器/网络意外断开；
-   LLM 上游失败；
-   RAG 检索依赖失败；
-   服务端内部异常。

客户端断开不应自动等同于业务失败。消息状态枚举、部分生成文本是否保存、取消操作是否提供独立接口，仍需在消息状态机设计中补齐。

## 9. 图片与附件资源 API

系统有两类图片资源，必须分开访问。

### 9.1 用户上传附件

``` http
GET /api/v1/attachments/{attachment_id}
```

用于读取用户在对话提问时上传的图片。

### 9.2 PDF 解析提取的图片

``` http
GET /api/v1/documents/{document_id}/assets/{asset_id}
```

用于读取 MinerU 从 PDF 中提取的图片。

### 9.3 资源访问约束

-   后端通过 SQLite 记录定位实际文件。
-   不向前端暴露本地文件系统路径。
-   校验资源是否存在及其归属关系。
-   PDF 图片必须属于指定文档的有效解析版本。
-   文档删除后，其 PDF 图片不可继续访问。
-   历史消息中已经保存的来源文字快照继续保留。
-   用户上传附件与 PDF 解析图片不混用附件记录或生命周期。

文件响应应设置正确的
`Content-Type`、必要的缓存策略及安全响应头。是否支持 Range
请求、缩略图和下载文件名待实现阶段确定。

## 10. 用户设置 API

  方法    路径                 说明
  ------- -------------------- ------------------
  GET     `/api/v1/settings`   获取全部用户设置
  PATCH   `/api/v1/settings`   更新用户设置

设置数据保存在 SQLite `user_settings`
表中，可包含主题（浅色、深色、跟随系统）、对话偏好和界面偏好等非敏感设置。

禁止通过该接口读取或修改：

-   DeepSeek API Key；
-   MinerU Token；
-   Embedding 模型等系统级配置；
-   访问密码、Cookie 签名密钥等安全配置。

第一版系统配置直接在后端配置文件中维护，不提供配置导入或导出。

## 11. 系统健康检查 API

### 11.1 接口清单

  方法   路径                                      说明
  ------ ----------------------------------------- --------------------------
  GET    `/api/v1/system/health`                   获取最近一次健康检查结果
  POST   `/api/v1/system/health/check`             手动检查全部依赖
  POST   `/api/v1/system/health/check/{service}`   手动检查指定依赖

检查对象：

-   MinerU
-   DeepSeek
-   Kafka
-   Elasticsearch
-   SQLite

状态枚举：

-   `healthy`：正常；
-   `unhealthy`：检查失败；
-   `unknown`：尚未检查。

### 11.2 结果保存与执行语义

-   健康检查结果保存在 FastAPI 进程内存缓存。
-   定时检查和手动检查都会更新缓存。
-   服务重启后状态恢复为 `unknown`。
-   不增加 SQLite 健康检查记录表。
-   不将健康检查结果写入 `user_settings`。
-   支持定时检查和手动检查；系统启动时不自动检查依赖。

**待补充：**
手动检查接口的异步执行响应结构、检查中状态、并发检查合并策略、检查超时和定时频率。当前接口清单尚未定义独立的检查任务查询端点，因此实现时应避免返回一个前端无法追踪的长时间后台任务。

## 12. 系统统计 API

``` http
GET /api/v1/system/statistics
```

统一返回文档统计和问答统计。

示例：

``` json
{
  "success": true,
  "data": {
    "documents": {
      "total": 100,
      "pending": 5,
      "processing": 2,
      "completed": 90,
      "failed": 3,
      "total_size_bytes": 1073741824
    },
    "chat": {
      "question_count": 200,
      "answer_count": 195,
      "total_tokens": 500000,
      "llm_failure_count": 5
    }
  },
  "error": null
}
```

数据来源：

  统计项                           数据来源
  -------------------------------- ---------------------------
  文档数量、状态数量、文件总大小   SQLite `documents`
  问题数量、回答数量               SQLite `messages`
  Token 总量、LLM 失败次数         SQLite `llm_call_records`

约束：

-   不依赖 Elasticsearch 统计结果。
-   第一版不提供费用估算。
-   第一版不提供趋势分析。
-   统计口径（例如删除文档是否计入历史累计、失败调用是否计入
    Token）需在统计 SQL 设计时形成明确规则。

## 13. 系统操作日志 API

### 13.1 接口清单

  方法   路径                         说明
  ------ ---------------------------- ------------------
  GET    `/api/v1/system/logs`        分页查询操作日志
  GET    `/api/v1/system/logs/{id}`   查看单条日志详情

列表支持：

-   页码分页；
-   按操作类型筛选；
-   按操作结果筛选；
-   按时间范围筛选；
-   按时间倒序排列。

约束：

-   基础系统操作日志保留最近 90 天，自动清理过期记录。
-   只通过网页查看。
-   第一版不提供日志下载或导出。
-   不增加面向用户的日志删除接口。
-   日志内容必须脱敏，不返回 API Key、MinerU Token、访问密码、Cookie 或
    CSRF Token。

## 14. 幂等性设计

### 14.1 适用接口

第一版明确对发送问答请求实施幂等控制：

``` http
POST /api/v1/conversations/{conversation_id}/messages
Idempotency-Key: <unique-key>
```

### 14.2 规则

1.  前端每次发起一个新的问答操作时生成唯一 Key。
2.  后端记录 Key 与创建的用户消息之间的关联。
3.  相同 Key、相同请求内容重复提交时，不重复创建消息或启动模型调用。
4.  相同 Key、不同请求内容时返回 `409 Conflict`。
5.  用户明确发起新问题时必须生成新 Key。
6.  重复提交时，若原请求仍在执行，返回已有消息 ID
    和执行状态，不重新启动生成。

### 14.3 数据库实现决策

在现有 `messages` 表增加：

-   `idempotency_key`
-   `request_hash`

对非空 `idempotency_key` 建立唯一索引，不新增独立幂等数据表。

### 14.4 待补充细节

-   幂等 Key 的作用域：建议至少与 conversation
    关联，并明确是否包含认证主体/端点。
-   `request_hash` 的规范化输入：需覆盖文本、附件 ID
    或附件内容摘要等影响业务结果的字段。
-   相同 Key 重复请求时，SSE 如何处理：当前决策要求返回已有消息 ID
    和状态，但尚未定义是否重新订阅原流、返回状态快照，或仅由客户端另行查询。
-   幂等记录保留期限：当前未确认清理期限；消息记录存在期间应避免 Key
    被意外复用。
-   并发相同 Key
    请求必须依赖数据库唯一约束和事务处理，不能只靠应用内先查后写。

## 15. 统一错误码

### 15.1 命名规范

格式：`模块名称_具体错误`。

错误码由后端集中定义；前端按错误码处理，不依赖错误消息文本。

### 15.2 已确定的错误码示例

  错误码                            HTTP 含义
  ------------------------------- ------ -----------------------
  `AUTH_INVALID_PASSWORD`            401 密码错误
  `AUTH_REQUIRED`                    401 尚未认证
  `CSRF_INVALID_TOKEN`               403 CSRF Token 无效
  `DOCUMENT_NOT_FOUND`               404 文档不存在
  `DOCUMENT_ALREADY_PROCESSING`      409 文档正在解析
  `FILE_TOO_LARGE`                   413 文件超过限制
  `INVALID_FILE_TYPE`                422 文件类型不支持
  `CONVERSATION_NOT_FOUND`           404 对话不存在
  `MESSAGE_NOT_FOUND`                404 消息不存在
  `IDEMPOTENCY_KEY_CONFLICT`         409 相同 Key 对应不同请求
  `LLM_REQUEST_FAILED`               502 模型调用失败
  `ELASTICSEARCH_UNAVAILABLE`        503 Elasticsearch 不可用
  `INTERNAL_SERVER_ERROR`            500 内部错误

这是一组已提出并确认采用统一规范的代表性错误码，不是完整错误码全集。实现时还需补充
Kafka、MinerU、文件存储、解析任务、资源归属、请求参数等错误码。

## 16. Request ID 请求追踪

### 16.1 规则

-   请求头：`X-Request-ID`。
-   前端可以传入 Request ID。
-   后端必须校验格式和长度；未提供或不合法时由后端生成新的 ID。
-   后端日志统一记录 Request ID。
-   普通 JSON 响应在顶层返回 `request_id`。
-   SSE 响应通过 `X-Request-ID` 响应头返回。
-   错误响应也必须包含 Request ID，便于定位日志。

普通 JSON 响应示例：

``` json
{
  "success": false,
  "data": null,
  "error": {
    "code": "LLM_REQUEST_FAILED",
    "message": "模型服务暂时不可用",
    "details": null
  },
  "request_id": "req_20261114_001"
}
```

SSE 响应头示例：

``` http
X-Request-ID: req_20261114_001
Content-Type: text/event-stream; charset=utf-8
```

Request ID 是一次 HTTP 请求的追踪标识，不等同于 `message_id`、`task_id`
或
`Idempotency-Key`。一次问答可能关联多个内部任务和模型调用，应通过日志字段建立关联。

## 17. API 接口总表

  ------------------------------------------------------------------------------------------------------------
  模块              方法              路径                                                  说明
  ----------------- ----------------- ----------------------------------------------------- ------------------
  认证              GET               `/api/v1/auth/status`                                 认证状态、CSRF
                                                                                            Token

  认证              POST              `/api/v1/auth/login`                                  登录

  认证              POST              `/api/v1/auth/logout`                                 退出

  文档              POST              `/api/v1/documents/upload`                            批量上传 PDF

  文档              GET               `/api/v1/documents`                                   文档列表

  文档              GET               `/api/v1/documents/{document_id}`                     文档详情

  文档              GET               `/api/v1/documents/{document_id}/tasks`               解析任务历史

  文档              DELETE            `/api/v1/documents/{document_id}`                     删除单个文档

  文档              POST              `/api/v1/documents/{document_id}/reparse`             重新解析

  文档              DELETE            `/api/v1/documents`                                   清空知识库

  对话              POST              `/api/v1/conversations`                               创建对话

  对话              GET               `/api/v1/conversations`                               对话列表

  对话              GET               `/api/v1/conversations/{conversation_id}`             对话详情

  对话              PATCH             `/api/v1/conversations/{conversation_id}`             修改标题/置顶

  对话              DELETE            `/api/v1/conversations/{conversation_id}`             删除对话

  消息              GET               `/api/v1/conversations/{conversation_id}/messages`    消息列表

  消息              POST              `/api/v1/conversations/{conversation_id}/messages`    发送消息、SSE 问答

  消息              PATCH             `/api/v1/messages/{message_id}`                       编辑用户消息

  消息              POST              `/api/v1/messages/{message_id}/retry`                 重新生成 AI 回答

  消息              DELETE            `/api/v1/messages/{message_id}`                       删除消息

  消息              PUT               `/api/v1/messages/{message_id}/feedback`              消息评价

  附件              GET               `/api/v1/attachments/{attachment_id}`                 用户上传图片

  文档资源          GET               `/api/v1/documents/{document_id}/assets/{asset_id}`   PDF 提取图片

  设置              GET               `/api/v1/settings`                                    获取用户设置

  设置              PATCH             `/api/v1/settings`                                    更新用户设置

  系统              GET               `/api/v1/system/health`                               最近健康检查结果

  系统              POST              `/api/v1/system/health/check`                         检查全部依赖

  系统              POST              `/api/v1/system/health/check/{service}`               检查单个依赖

  系统              GET               `/api/v1/system/statistics`                           系统统计

  日志              GET               `/api/v1/system/logs`                                 操作日志列表

  日志              GET               `/api/v1/system/logs/{id}`                            操作日志详情
  ------------------------------------------------------------------------------------------------------------

## 18. 中间件与后端分层建议

以下是实现建议，用于落实已确认的 API 规范，不构成新增业务功能：

### 18.1 中间件/公共依赖

建议按职责拆分：

1.  Request ID 中间件：生成/校验并注入请求追踪 ID。
2.  认证依赖：校验签名 Cookie 和有效期。
3.  CSRF 依赖：校验状态变更请求的 CSRF Token。
4.  全局异常处理器：将已知业务异常映射为统一错误结构。
5.  请求日志：记录方法、路径、状态码、耗时和 Request ID，敏感字段脱敏。

### 18.2 路由模块建议

``` text
app/
  api/
    v1/
      auth.py
      documents.py
      conversations.py
      messages.py
      attachments.py
      settings.py
      system.py
      logs.py
  schemas/
    common.py
    auth.py
    document.py
    conversation.py
    message.py
    attachment.py
    settings.py
    system.py
  services/
    auth_service.py
    document_service.py
    conversation_service.py
    message_service.py
    health_service.py
    statistics_service.py
    log_service.py
  middleware/
    request_id.py
```

路由层负责 HTTP 协议、参数校验和响应；Service
层负责业务编排；Repository/DAO 层负责 SQLite
等数据访问。路由不应直接承担复杂的跨组件事务和文件清理逻辑。

## 19. 关键业务时序

### 19.1 PDF 上传与解析

``` text
React 前端
  |
  | POST /documents/upload (multipart/form-data)
  v
FastAPI
  |
  | 校验数量、大小、PDF 内容
  | 保存原始文件
  | SQLite 创建 document / version / parse_task
  | 可靠投递记录
  v
Kafka
  |
  v
Worker
  |
  | 调用 MinerU VLM API
  | 轮询解析状态、下载解析产物
  | 后处理、保存图片和 Chunk
  | 生成 Embedding
  | 写入 Elasticsearch
  | 校验写入结果
  v
SQLite
  |
  | 更新版本和文档状态为 completed
  v
React 前端通过文档列表/详情接口查看状态
```

此流程是业务职责示意。SQLite 与 Kafka 之间的可靠投递必须遵循项目已确定的
Outbox/恢复设计，不能假设跨系统写入天然具备原子性。

### 19.2 多模态 RAG 问答

``` text
React
  |
  | POST /conversations/{id}/messages
  | multipart/form-data + Idempotency-Key
  v
FastAPI
  |
  | 认证、CSRF、幂等校验
  | 保存用户消息及图片附件
  | Query Rewrite / 检索准备
  v
Elasticsearch
  |
  | BM25 + Dense Vector + 原生 RRF
  v
FastAPI RAG 编排
  |
  | 组织文本、图片和来源上下文
  v
DeepSeek API
  |
  | 流式生成
  v
SSE
  |
  | message_start
  | content_delta
  | sources
  | message_complete / error
  v
React
```

若 Elasticsearch
检索整体超时或失败，按已确认的检索策略有限重试一次；仍失败则终止本次 RAG
问答，不允许跳过检索直接请求 LLM。单路检索正常为空不视为系统异常。

## 20. 待补充与实现前检查清单

以下项目尚未形成完整、明确的接口级决策。实现前应补齐，不能把示例或建议误当成已确认要求。

### 20.1 请求/响应 Schema

-   [ ] 每个端点的完整 Pydantic 请求模型和响应模型。
-   [ ] 所有字段的必填性、长度、范围、默认值和 nullable 规则。
-   [ ] 统一时间格式、ID 格式和枚举值。
-   [ ] 列表排序的稳定次级排序规则。
-   [ ] 空列表、空详情和资源不存在的统一语义。

### 20.2 文档与任务

-   [ ] 上传文件名冲突、损坏 PDF、加密 PDF 的处理。
-   [ ] 批量上传整体请求体超限时的行为。
-   [ ] 上传成功但 Kafka 投递暂未完成时，接口如何准确表达状态。
-   [ ] 删除/清空知识库的超时、部分失败和重复调用语义。
-   [ ] 重新解析的并发冲突响应和版本切换原子性。

### 20.3 消息与 SSE

-   [ ] 消息状态机及可见状态枚举。
-   [ ] SSE 断连后的持久化策略与客户端恢复方式。
-   [ ] `retry` 接口是否使用同一 SSE 协议。
-   [ ] 重复 Idempotency-Key 请求的 SSE 恢复/订阅方式。
-   [ ] 编辑消息时附件如何替换。
-   [ ] 删除单条消息是否影响其后的消息。
-   [ ] `sources`
    事件的完整字段，包括文档、版本、页码、Chunk、图片资源引用和引用快照。
-   [ ] SSE 心跳、代理缓冲关闭、超时及取消信号的实现约定。

### 20.4 安全与运维

-   [ ] 登录失败限制的次数、时间窗口和锁定策略。
-   [ ] Cookie 签名算法、密钥轮换及无状态登出限制的接受方式。
-   [ ] CSRF Token 的签名、绑定、过期和轮换规则。
-   [ ] CORS、HTTPS、反向代理和可信 Host 配置。
-   [ ] 健康检查异步任务的状态返回及并发控制。
-   [ ] 操作日志字段、敏感字段脱敏规则和 90 天清理执行方式。
-   [ ] 统计数据的精确定义和去重口径。

### 20.5 API 文档与测试

-   [ ] 由 FastAPI 生成 OpenAPI 文档，并检查路径、Schema、状态码。
-   [ ] 为每个接口编写成功、参数错误、未认证、资源不存在和依赖失败测试。
-   [ ] 测试批量上传部分成功。
-   [ ] 测试相同幂等键并发提交。
-   [ ] 测试 SSE 正常结束、模型异常、检索异常和客户端断开。
-   [ ] 测试文档删除与 Worker 并发写入的竞态。
-   [ ] 测试 CSRF 缺失/错误、Cookie 过期和登录限流。
-   [ ] 测试 Request ID 在日志、JSON 响应和 SSE 响应头之间的一致性。

## 21. 决策记录索引

  --------------------------------------------------------------------------------------------
  决策编号                主题                                         状态
  ----------------------- -------------------------------------------- -----------------------
  ARCH-API-01             RESTful、版本前缀、JSON、multipart、POST +   已确认
                          SSE、FastAPI + Pydantic                      

  ARCH-API-02             普通 JSON 统一响应结构                       已确认

  ARCH-API-03             页码分页，默认 20、最大 100                  已确认

  ARCH-API-04             访问密码 + HttpOnly Cookie                   已确认

  ARCH-API-05             无状态签名 Cookie，12 小时有效期             已确认

  ARCH-API-06             批量 PDF 上传、逐文件结果、允许部分成功      已确认

  ARCH-API-07             统一文档列表接口                             已确认

  ARCH-API-08             文档详情与任务历史分开查询                   已确认

  ARCH-API-09             删除、重新解析、清空知识库接口               已确认

  ARCH-API-10             删除同步执行                                 已确认

  ARCH-API-11             五个对话管理接口                             已确认

  ARCH-API-12             六个消息管理接口                             已确认

  ARCH-API-13             提问时直接上传图片                           已确认

  ARCH-API-14             SSE 事件协议                                 已确认

  ARCH-API-15             用户附件与 PDF 图片分开访问                  已确认

  ARCH-API-16             统一用户设置 GET/PATCH                       已确认

  ARCH-API-17             三个系统健康检查接口                         已确认

  ARCH-API-18             健康检查结果使用进程内存缓存                 已确认

  ARCH-API-19             统一系统统计接口                             已确认

  ARCH-API-20             操作日志列表与详情接口                       已确认

  ARCH-API-21             认证状态、登录、退出接口                     已确认

  ARCH-API-22             服务端验证 CSRF Token                        已确认

  ARCH-API-23             问答 Idempotency-Key 幂等控制                已确认

  ARCH-API-24             统一错误码规范                               已确认

  ARCH-API-25             Request ID 请求追踪                          已确认
  --------------------------------------------------------------------------------------------

------------------------------------------------------------------------

## 附录 A：实现原则

1.  先落实已确认的 API
    契约，再补齐待确认细节；不要在编码时静默改变已确认决策。
2.  HTTP 层只负责协议和输入输出，复杂业务逻辑放在 Service 层。
3.  SQLite 是业务状态的权威来源；Elasticsearch
    是检索索引，不能反向替代业务状态。
4.  Kafka 是异步任务传递机制；消息投递失败、重复投递和 Worker
    重试必须具备幂等与恢复能力。
5.  对外 API 不暴露内部实现细节，包括 Kafka Offset、Worker
    租约、服务器文件路径和密钥。
6.  文档删除、重新解析、问答重试等操作必须考虑并发、重复请求和外部依赖失败。
7.  普通
    JSON、SSE、文件响应三种响应形态应明确区分，前端分别实现对应消费逻辑。
8.  所有错误均应可通过错误码和 Request ID 定位；日志不得泄漏敏感数据。

## 附录 B：版本变更记录

  ------------------------------------------------------------------------
  版本                    日期                    说明
  ----------------------- ----------------------- ------------------------
  V1.0                    2026-11-14              汇总 ARCH-API-01 ～
                                                  ARCH-API-25
                                                  已确认决策，形成第一版
                                                  API 设计基线

  ------------------------------------------------------------------------
