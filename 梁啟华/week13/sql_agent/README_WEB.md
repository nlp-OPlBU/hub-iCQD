# SQL Agent Web 前端与接口

在原有 `sql_agent` 项目上增加一个启动后可在浏览器直接访问的聊天式网页：输入中文问题，查看 Agent 的最终回答、生成的 SQL、折叠的执行过程，以及查询结果表格。

原有 `api.py`、`sql_agent.py`、`langgraph_agent.py`、`db_parser.py`、`llm.py` **均未修改**。新后端直接导入 `LangGraphSQLAgent`（默认）或 `SQLAgent`（classic），没有重写一套并行的 Agent 逻辑。

## 1. 新增文件

| 文件 | 用途 |
| --- | --- |
| `sql_agent/web_app.py` | FastAPI 服务：会话/对话、健康检查、上下文管理、超时与错误映射、托管静态页面 |
| `sql_agent/frontend/index.html` | 页面入口（聊天界面） |
| `sql_agent/frontend/styles.css` | 页面样式（含移动端适配） |
| `sql_agent/frontend/app.js` | 前端交互（发送、消息渲染、执行过程、SQL、结果表格） |
| `sql_agent/README_WEB.md` | 本说明 |

## 2. 快速开始

### 2.1 安装依赖

已在 Python 3.13.5 环境验证，依赖版本：

| 包 | 实测版本 | 用途 |
| --- | --- | --- |
| `langgraph` | 1.2.20 | LangGraph 状态图 |
| `langchain-core` | 1.7.6 | 消息类型 |
| `langchain-openai` | 1.7.4 | OpenAI 兼容接口 |
| `fastapi` | 0.162.0 | HTTP 接口 |
| `uvicorn` | 0.54.0 | ASGI 服务 |
| `httpx` | 0.28.1 | 前端/测试请求 |

```powershell
pip install fastapi "uvicorn[standard]" langgraph langchain-core langchain-openai httpx
```

### 2.2 配置环境变量

| 变量 | 是否必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | 真实模式必填 | 无 | DashScope / OpenAI 兼容 Key |
| `OPENAI_BASE_URL` | 否 | `https://dashscope.aliyuncs.com/compatible-mode/v1` | 模型接口地址 |
| `SQL_AGENT_MODEL` | 否 | `qwen-max` | 模型名，可按账号权限改为 `qwen-flash` 等 |
| `SQL_AGENT_DB_PATH` | 建议显式配置 | `Week13/04_SQL-Code-Agent-Demo/chinook.db` | SQLite 数据库路径 |
| `SQL_AGENT_ENGINE` | 否 | `graph` | `graph`=LangGraph，`classic`=原线性实现 |
| `SQL_AGENT_MAX_REWRITES` | 否 | `2` | SQL 校验失败后的最大改写次数 |
| `SQL_AGENT_MAX_ROWS` | 否 | `200` | 单次查询最多返回行数 |
| `SQL_AGENT_SEMANTIC_CHECK` | 否 | `1` | 设为 `0` 可跳过 LLM 语义校验（更快、更省调用） |
| `SQL_AGENT_TIMEOUT` | 否 | `180` | 单次对话超时秒数，也可以 `--timeout` 指定 |
| `SQL_AGENT_MOCK` | 否 | `0` | 设为 `1` 进入离线模拟模式 |
| `WEB_SESSION_TTL_MINUTES` | 否 | `60` | 会话空闲过期时间 |
| `WEB_MAX_SESSIONS` | 否 | `200` | 进程内最多保留会话数 |
| `WEB_HISTORY_TURNS` | 否 | `5` | 参与上下文改写的最近问答轮数 |

> **数据库说明**：项目默认路径 `Week13/04_SQL-Code-Agent-Demo/chinook.db` 在当前工作区并不存在。请把 `chinook.db` 放到该位置，或用 `SQL_AGENT_DB_PATH` 指向已有数据库。本机可直接使用：
>
> `H:\八斗\八斗学院\7月份\第11周-ChatBI数据智能问答\Week12\04_SQL-Code-Agent-Demo\chinook.db`（11 张表）。

### 2.3 启动

```powershell
cd "H:\八斗\八斗学院\7月份\第13周：Agent设计模式（LangGraph、LangChain）和复杂后端应用\Week13\sql_agent"

$env:OPENAI_API_KEY = "sk-你的Key"
$env:SQL_AGENT_MODEL = "qwen-max"
$env:SQL_AGENT_DB_PATH = "H:\八斗\八斗学院\7月份\第11周-ChatBI数据智能问答\Week12\04_SQL-Code-Agent-Demo\chinook.db"

python web_app.py --port 8000
```

打开：

- 页面入口：<http://127.0.0.1:8000/>
- 接口文档：<http://127.0.0.1:8000/docs>

也可以用 uvicorn 启动：

```powershell
python -m uvicorn web_app:app --host 127.0.0.1 --port 8000
```

没有可用 API Key 时，可用离线模拟模式验证界面与接口：

```powershell
python web_app.py --port 8000 --mock `
  --db "H:\八斗\八斗学院\7月份\第11周-ChatBI数据智能问答\Week12\04_SQL-Code-Agent-Demo\chinook.db"
```

模拟模式仍会真实执行生成的 SQL、读取真实数据库，只是 LLM 调用被本地规则替代，回答会明确标注“模拟数据”。

## 3. 页面功能

- 聊天式问答：输入框 + 消息列表，用户问题与 Agent 回答分列显示。
- 折叠的“执行过程”：按阶段展示 `数据库解析 → table 选择 → SQL 生成 → SQL 校验 → SQL 执行 → 结果汇总`；多轮追问时前面会多一步“上下文理解”。
- 折叠的“查看 SQL”：展示最终 SQL，支持一键复制；并显示生成/改写次数。
- 查询结果：字段名作为表头，多行数据在固定高度区域内滚动，支持横向滚动；空结果显示提示。
- 顶部状态：在线 / 模拟模式 / 离线；模拟模式和初始化失败会在页面顶部给出提示条。
- “新对话”：清空页面消息并重置服务端会话上下文。
- 移动端：单列布局、输入框随内容增高、表格可滚动、`100dvh` + 安全区适配。

## 4. 接口列表

统一前缀 `/api`，响应均为 JSON。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 健康检查：Agent、数据库、模型配置、表清单 |
| `POST` | `/api/chat` | 对话：输入问题，返回回答、SQL、执行轨迹与结果集 |
| `DELETE` | `/api/session/{session_id}` | 清空指定会话的上下文 |
| `GET` | `/` | 聊天页面 |
| `GET` | `/docs` | Swagger 文档 |

### 4.1 `GET /api/health`

```json
{
  "status": "ok",
  "mock": false,
  "engine": "LangGraphSQLAgent",
  "db_path": "H:\\...\\chinook.db",
  "model": "qwen-max",
  "api_key_configured": true,
  "timeout_seconds": 180.0,
  "table_count": 11,
  "tables": [{"name": "customers", "row_count": 59}],
  "error": null
}
```

Agent 初始化失败（缺少 Key、数据库不存在等）时返回 `503`：

```json
{"status": "error", "error": "未配置大模型 API Key，请先设置环境变量 OPENAI_API_KEY"}
```

### 4.2 `POST /api/chat`

请求：

```json
{
  "message": "customers 表一共有多少条记录？",
  "session_id": "可选，不传则新建会话"
}
```

响应：

```json
{
  "session_id": "4ce314715a3c48e083a13e7ada802fd0",
  "question": "customers 表一共有多少条记录？",
  "resolved_question": "customers 表一共有多少条记录？",
  "answer": "customers 表共有 59 条记录。",
  "sql": "SELECT COUNT(*) AS 记录数 FROM \"customers\"",
  "tables": ["customers"],
  "attempts": 1,
  "columns": ["记录数"],
  "rows": [[59]],
  "row_count": 1,
  "truncated": false,
  "elapsed_ms": 1200.5,
  "mock": false,
  "trace": [
    {"stage": "数据库解析", "detail": "共 11 张表、11 条外键关系", "payload": {}},
    {"stage": "table 选择", "detail": "选中 customers", "payload": {}},
    {"stage": "SQL 生成", "detail": "SELECT ...", "payload": {}},
    {"stage": "SQL 校验", "detail": "通过", "payload": {}},
    {"stage": "SQL 执行", "detail": "返回 1 行", "payload": {}},
    {"stage": "结果汇总", "detail": "...", "payload": {}}
  ]
}
```

- `rows` 最多返回 `SQL_AGENT_MAX_ROWS` 行，超出时 `truncated=true`。
- 同一 `session_id` 内会保留最近 `WEB_HISTORY_TURNS` 轮问答。追问（如“那 tracks 表呢？”）会先让模型改写为独立问题，`resolved_question` 展示改写结果，`trace` 第一条为“上下文理解”。

### 4.3 `DELETE /api/session/{session_id}`

```json
{"session_id": "...", "cleared": true}
```

### 4.4 错误码

| HTTP | `code` | 含义 |
| --- | --- | --- |
| `400` | `invalid_request` | 参数不合法 |
| `422` | `sql_agent_error` | SQL 多轮改写后仍未通过校验/执行 |
| `422` | - | 请求体校验失败（如问题为空） |
| `500` | `database_error` | SQLite 执行错误 |
| `502` | `llm_error` | 模型鉴权/调用失败 |
| `503` | `agent_unavailable` | Agent 未初始化（缺 Key、数据库不存在等） |
| `504` | `timeout` | 单次对话超时 |

错误响应体统一为 `{"detail": "可读错误信息", "code": "..."}`；前端会以红色消息卡片展示，并给出排查建议。

## 5. 验证状态

已实际启动后端（端口 8010，模拟模式）并验证：

- `GET /api/health`：200，11 张表，`engine=LangGraphSQLAgent`。
- 多轮对话（真实 chinook 数据库，真实 SQL 执行）：
  - 第 1 轮：“customers 表一共有多少条记录？”→ `SELECT COUNT(*) AS 记录数 FROM "customers"` → `[[59]]`。
  - 第 2 轮（同一会话）：“那 tracks 表呢？”→ 自动改写为“tracks 表一共有多少条记录？”→ `SELECT COUNT(*) AS 记录数 FROM "tracks"` → `[[3503]]`；`trace` 首项为“上下文理解”。
  - 其余示例：“发票总金额是多少？”→ `SELECT SUM(total) AS 总金额 FROM "invoices"` → `[[2328.6]]`；“不同国家的客户数量分别是多少？”→ `GROUP BY country` → 24 行，第一行 `["USA", 13]`。
- 页面 `/`、`/styles.css`、`/app.js`、`/docs` 均返回 200；浏览器实测可输入问题、显示回答、展开 SQL、查看结果表格；执行过程区域包含 6 个步骤。
- 真实模式缺少 `OPENAI_API_KEY`：`/api/health` 与 `/api/chat` 均返回 503，并给出可读原因。
- 把 `SQL_AGENT_TIMEOUT` 设为极小值：`/api/chat` 返回 504，提示“处理超时”。

**未完成真实验证的部分**：当前环境没有有效的大模型 Key。`Week13` 教程目录中发现的 3 个唯一明文 DashScope Key 均已实测返回 `401 Incorrect API key`，因此**真实模型链路（LLM 选表 / 生成 SQL / 汇总回答）未能端到端验证**；上述链路只验证到“真实 SQL 执行 + 模拟 LLM”这一层。配置有效 Key 后，同一接口即可切换到真实链路，无需改代码。

## 6. 安全提醒

`Week13` 下多个教程文件（如 `01_langchain教程/01_调用模型.py`、`03_deepagent教程/01_基础案例.py` 等，共 15 个文件）直接写有明文 DashScope API Key（3 个唯一值，均已失效）。新代码没有复制这些 Key，只从 `OPENAI_API_KEY` 环境变量读取。建议删除这些明文 Key，并在阿里云百炼控制台确认其已吊销/轮换。

## 7. 实现说明与限制

- **线程模型**：SQLite 连接默认只能在创建它的线程使用，因此 Agent 在单线程 `ThreadPoolExecutor` 中创建并执行；所有问答与健康检查都走同一线程。优点是连接安全，代价是所有请求串行。
- **超时**：`asyncio.wait_for` 只让接口及时返回 504，**不会中断**已在线程中运行的 LLM/SQL 调用；超时后该调用仍可能占用工作线程直到结束。
- **会话**：上下文保存在进程内存（LRU + TTL），服务重启即丢失；多进程部署时各进程不共享。
- **上下文改写**：只在检测到追问特征（短问题、代词、“那…呢”等）时触发一次额外 LLM 调用；改写失败会自动回退为原问题，不影响主流程。
- **未实现**：流式输出、登录鉴权、限流、并发隔离、LangGraph checkpoint 持久化。请默认绑定 `127.0.0.1`，不要直接暴露到公网。
- `api.py` 仍然可用（无状态 `/query` 等接口）；`web_app.py` 是独立入口，二者互不影响。
