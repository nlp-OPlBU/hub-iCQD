# FastAPI 后端接口设计

本设计已据用户审查后的指示实现。API 复用现有工作流，保留 CLI 和业务输出契约；安装、启动与验证环境见 [REMADE](../REMADE.md)。

## 1. 首版范围与结构

为已有 `SQLAgentWorkflow.run(question)` 增加 HTTP 入口。一次请求等待一次查询完成，无会话记忆。首版面向本机使用，默认监听 `127.0.0.1`，不启用跨域访问；暂不加入用户系统、数据库上传、直接执行 SQL、流式输出或任务队列。

```text
Week13/
├── docs/
│   └── api-design.md          # 本设计及后续验收标准
├── src/sql_agent/api/
│   ├── __init__.py            # API 包标记
│   ├── app.py                 # create_app、lifespan、路由与异常处理注册
│   ├── dependencies.py        # 从 app.state 获取工作流，支持测试替换
│   ├── schemas.py             # 请求、健康检查、HTTP 错误模型
│   ├── errors.py              # HTTP 异常处理、工作流失败转换
│   └── routes/
│       ├── __init__.py        # 路由包标记
│       ├── health.py          # 存活与就绪检查
│       └── queries.py         # 自然语言查询路由
└── tests/api/
    ├── conftest.py            # API 测试客户端与异步工作流替身
    └── test_routes.py         # 接口契约与异常分支测试
```

上述文件已实现。已有 `workflow.py` 是业务编排层，首版不再叠加重复的 service/repository 层。API 配置仍集中在已有 `config.py`，不新建第二套配置。

## 2. 接口总览

| 方法 | 路径 | 用途 | 主要返回码 |
| --- | --- | --- | --- |
| POST | `/api/v1/queries` | 提交自然语言问题，等待最终答案 | 200 / 415 / 422 / 500 / 503 |
| GET | `/healthz` | 检查 HTTP 服务存活，不访问模型或数据库 | 200 |
| GET | `/readyz` | 检查工作流已初始化、数据库可只读连接 | 200 / 503 |
| GET | `/docs` | 交互式接口文档 | 200 |
| GET | `/openapi.json` | 机器可读的接口契约 | 200 |

业务路径加 `/api/v1`，探针路径保持独立。`/docs` 与 `/openapi.json` 使用框架能力，不单独创建路由文件。

## 3. 自然语言查询

### 请求

`POST /api/v1/queries`，`Content-Type: application/json`：

```json
{
  "question": "列出销量最高的 5 首歌曲"
}
```

仅接受 `question`：严格字符串，去除首尾空白后长度为 1～2000 个字符。缺失、null、非字符串、全空白、超长、非法 JSON 或多余字段均返回 422。请求体不是 JSON 媒体类型时返回 415。

支持 `application/json`（含 charset 参数）和 `application/*+json`；缺少 Content-Type 也返回 415。字段校验仅将已知的 `question` 位置作为字段名，其他错误统一标记 `body`，不回显额外字段名或请求值。路径不自动补尾部斜杠。

客户端不能传数据库路径、模型密钥、原始 SQL、修复次数或执行限制，这些由服务端配置控制。空白输入在 HTTP 边界返回 422；这不改变 CLI 对空问题的现有处理。

### 成功响应

HTTP 200，直接复用已有 `WorkflowOutput`，不增加一层 `data`，不复制定义同名字段。下面是澄清分支的结构示例，非实际调用记录：

```json
{
  "question": "查一下排名",
  "answer": "请说明要按销量还是销售额排名，以及排名对象。",
  "sql": null,
  "tables": [],
  "row_count": 0,
  "truncated": false,
  "repair_attempts": 0,
  "evidence": [],
  "caveats": [],
  "error": null
}
```

| 情况 | HTTP 行为 |
| --- | --- |
| 查询成功 | 200，返回答案、实际 SQL、表名、证据等现有字段 |
| 需要澄清或选表无法确定 | 200，`answer` 给出提示，`sql` 为 null，`error` 为 null |
| 查询为空 | 200，`row_count` 为 0，返回现有空结果答复 |
| 汇总失败后表格降级 | 200，保留答案及 `caveats` 中的降级说明 |
| `WorkflowOutput.error` 非空 | 500，转换为下述 `workflow_failed` HTTP 错误 |

`row_count` 仍为执行器实际保留的结果行数，`truncated` 仍仅表示执行器行数截断。首版不返回原始行数组，不把汇总范围当作完整数据。当前输出没有显式的澄清状态字段，首版客户端展示 `answer` 并允许重新提交完整问题；不通过解析答案文本推测状态，不声称支持接续对话。

### HTTP 错误

HTTP 层失败统一使用以下结构，和成功响应通过 HTTP 状态区分：

```json
{
  "error": {
    "code": "validation_error",
    "message": "请求参数不合法。",
    "details": [
      {"field": "question", "message": "问题不能为空。"}
    ]
  }
}
```

| 状态码 | `error.code` | 含义 |
| --- | --- | --- |
| 415 | `unsupported_media_type` | 查询请求不是 JSON 媒体类型 |
| 422 | `validation_error` | JSON 或字段校验失败 |
| 500 | `workflow_failed` | 工作流返回非空 `error`，包含修复耗尽、cannot_fix 或模型调用失败 |
| 500 | `internal_error` | API 层未预期异常 |
| 503 | `service_unavailable` | 工作流未就绪或数据库暂不可用 |
| 404 | `not_found` | 路径不存在 |
| 405 | `method_not_allowed` | 请求方法不支持 |

`details` 始终为数组，无字段细节时返回 `[]`。500 对外给出固定、可读提示，原始异常写日志，不直接透传可能包含路径、模型端点或上游响应的异常文本。工作流失败时不返回半成品作为成功答案。现有工作流只保留字符串错误，首版不靠匹配字符串强行区分 502/504；SQLite 超时耗尽修复后同样映射为 `workflow_failed`。

每个响应附带服务端生成的 `X-Request-ID`，API 请求日志记录相同 ID，方便定位失败。校验错误不回显整个请求体。

## 4. 健康检查

- `GET /healthz`：200，`{"status":"ok"}`。只说明进程能够响应 HTTP。
- `GET /readyz`：工作流已初始化且数据库可只读连接并执行轻量 `SELECT 1` 时，200，`{"status":"ready"}`；否则 503，使用统一错误结构。
- 就绪检查不调用模型、不重新统计所有表，也不保证模型服务此刻可用。进程启动失败时没有 HTTP 响应，不能承诺还能访问探针。

## 5. 复用方式与实施注意事项

1. 用 `create_app()` 工厂组装应用，以 `APIRouter` 拆分查询与探针。采用 FastAPI 的 [多文件应用结构](https://fastapi.tiangolo.com/tutorial/bigger-applications/)。
2. 用 [lifespan](https://fastapi.tiangolo.com/advanced/events/) 管理应用拥有的目录、校验器和工作流实例，退出时释放 SQLAlchemy 引擎。初始化一次、每次调用新建查询状态；通过依赖注入替换测试工作流。
3. 当前包 `sql_agent/__init__.py` 导入工作流，进而初始化模块级 Agent。因此配置和密钥必须在导入前设置，不能把一切初始化都推迟到 lifespan 的说法当作已实现。首版保留该前提，启动错误明确记录，不顺带重构包导入行为。
4. 查询路由使用异步调用，复用 `await workflow.run(question)`，不在请求中调用 `asyncio.run()`。FastAPI 的 [异步文档](https://fastapi.tiangolo.com/async/) 说明，直接调用同步辅助函数不会自动在线程池中执行。经检查当前 LangGraph 及回归验证，`ainvoke()` 已将同步 SQL 校验与执行节点分派到线程池，因此无需修改工作流；API 自己的初始化和数据库预检通过 `run_in_threadpool` 执行，SQLite 连接在执行线程内使用并关闭。
5. 应用拥有的目录实例在启动时固定，数据库路径只从配置读取；首版不承诺数据库热切换。共享工作流不保存上一次查询状态，并用并发请求测试确认相互隔离。
6. 保留只读 URI、SQL 安全检查、EXPLAIN 校验、有限修复、行数限制及表格降级。路由不能绕过工作流直接执行生成 SQL。
7. 现有 `SQL_AGENT_QUERY_TIMEOUT` 只限制单次 SQLite 执行，不是 HTTP 总超时。首版请求一直等待工作流完成，不承诺固定总耗时；请求级超时与可靠取消作为后续独立扩展，不返回虚假的 504 保证。

## 6. 启动方式

新增运行依赖为 `fastapi`、`uvicorn`，测试客户端需要 `httpx`。本机已有这些依赖，无需安装；已验证版本与安装命令记录在 REMADE。

在已安装全部依赖并配置模型密钥的 PowerShell 中，使用同一个 Python 环境：

```powershell
$env:PYTHONPATH = (Join-Path (Get-Location) 'src')
python -m uvicorn sql_agent.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

`create_app` 工厂已实现。新增配置（如后续需要 CORS 白名单）统一进入 `config.py`，届时同步说明默认值。

## 7. 实施后的验收范围

- 异步工作流替身验证成功、澄清、空结果、汇总降级、工作流失败和未知异常的状态码及 JSON。
- 校验空白、超长、错误类型、多余字段、非法 JSON、非 JSON 媒体类型，确认不触发工作流。
- 验证存活检查不调用模型或数据库，就绪检查只进行轻量只读检查，失败返回 503。
- 验证响应模型、OpenAPI 中的错误响应声明、请求 ID、资源释放以及并发请求状态隔离。
- 若为消除阻塞而修改工作流，运行现有离线工作流回归，检查健康接口在慢查询期间仍能响应。
- 在同一 Python 环境运行 `python -m compileall -q src/sql_agent` 与 `python -m pytest tests -q`，全部模型调用使用替身，数据库使用临时库。

测试全部使用临时 SQLite 库及异步工作流或 Agent 替身，不依赖真实模型。接口错误与成功分支均通过 HTTP 客户端验证，真实 LangGraph 测试验证并发状态隔离及慢 SQL 期间的服务响应。离线测试不代表真实模型服务已通过端到端验证。
