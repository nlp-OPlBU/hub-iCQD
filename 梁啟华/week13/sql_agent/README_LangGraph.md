# SQL Agent（LangGraph 版）使用说明

## 1. 项目概述

`sql_agent` 原本是一个基于 SQLite 的「自然语言 → SQL → 结果解读」Agent，能力包括：

- 读取数据库 schema（表、字段、类型、主键、外键、字段统计、样例数据）
- 根据问题选择相关表
- 调用大模型生成 SQLite 查询
- SQL 校验：只读/单语句/语法校验 + 基于大模型的语义校验
- 执行 SQL 并截断大结果集
- 校验或执行失败后让模型改写 SQL 并重试
- 把查询结果汇总为自然语言回答，并记录全流程 trace

本次新增 `langgraph_agent.py`，用 LangGraph 的 `StateGraph` 把这些能力重新编排成一张显式状态图，包含条件分支和「校验失败 → 改写 → 再校验」的循环；原有 `SQLAgent`、`DBParser`、`LLMClient` 均保留，未破坏原有实现。

## 2. 目录结构

```
sql_agent/
├── db_parser.py                # 原有：schema 解析、SQL 只读校验、SQL 执行
├── llm.py                      # 原有：LangChain ChatOpenAI 封装（chat / chat_json）
├── sql_agent.py                # 原有：线性工作流、Prompt 模板、AgentResult
├── langgraph_agent.py          # 新增：LangGraph StateGraph 实现
├── api.py                      # 新增：FastAPI REST 接口
├── main.py                     # 小幅改动：新增 --engine graph|classic
├── tests/
│   ├── test_langgraph_agent.py # 核心链路测试（临时 SQLite + 假 LLM）
│   └── test_api.py             # REST 接口测试（FastAPI TestClient）
└── README_LangGraph.md         # 本说明
```

## 3. 环境依赖与安装

- Python 3.10+（本环境实测 Python 3.13.5）
- 依赖包：

| 包 | 实测版本 | 用途 |
| --- | --- | --- |
| `langgraph` | 1.2.20 | 状态图编排 |
| `langchain-core` | 1.7.6 | 消息类型等基础能力 |
| `langchain-openai` | 1.7.4 | 调用 OpenAI 兼容接口 |
| `fastapi` | 0.162.0 | REST 接口 |
| `uvicorn` | 0.54.0 | ASGI 服务启动 |
| `httpx` | 0.28.1 | TestClient 依赖 |
| `pytest` | 8.3.4 | 测试 |

安装：

```bash
pip install langgraph langchain-core langchain-openai fastapi "uvicorn[standard]" httpx pytest
```

> 如果暂时无法安装 `langgraph`，仍可用原有实现：
> `python main.py --engine classic ...`。`--engine classic` 不导入 LangGraph。

## 4. 配置项

### 4.1 大模型

通过环境变量配置（见 [llm.py](file:///h:/八斗/八斗学院/7月份/第13周：Agent设计模式（LangGraph、LangChain）和复杂后端应用/Week13/sql_agent/llm.py)）：

| 变量 | 是否必填 | 默认值 |
| --- | --- | --- |
| `OPENAI_API_KEY` | 必填 | 无，缺失时初始化报错 |
| `OPENAI_BASE_URL` | 可选 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| `SQL_AGENT_MODEL` | 可选 | `qwen-max` |

PowerShell 示例：

```powershell
$env:OPENAI_API_KEY = "sk-你的DashScopeKey"
$env:SQL_AGENT_MODEL = "qwen-max"   # 可选
```

### 4.2 数据库

- 默认路径：`Week13/04_SQL-Code-Agent-Demo/chinook.db`
  （即 [sql_agent.py](file:///h:/八斗/八斗学院/7月份/第13周：Agent设计模式（LangGraph、LangChain）和复杂后端应用/Week13/sql_agent/sql_agent.py) 中的 `DEFAULT_DB_PATH`）
- 仓库中未附带该数据库文件，请自行放置，或用 `--db` 指定任意 SQLite 文件。

### 4.3 运行参数

| 参数 | 说明 | 默认 |
| --- | --- | --- |
| `--engine` | `graph`=LangGraph 实现；`classic`=原有线性实现 | `graph` |
| `-q/--question` | 只执行单个问题，不传则进入交互模式 | 无 |
| `--db` | SQLite 数据库路径 | 上述默认路径 |
| `--max-rewrites` | SQL 校验/执行失败后的最大改写次数 | 2 |
| `--max-rows` | 单次查询最多返回行数 | 200 |
| `--no-semantic-check` | 跳过基于大模型的语义校验 | 关闭 |
| `--trace` | 打印完整工作流阶段记录 | 关闭 |
| `--quiet` | 只输出最终答案，不输出过程日志 | 关闭 |

## 5. 如何运行

### 5.1 主程序（LangGraph 引擎，默认）

```bash
python main.py -q "销售额最高的前 5 个国家是哪些？"
python main.py -q "每位客户的总消费是多少？" --trace
```

指定数据库：

```bash
python main.py --db ..\..\path\to\chinook.db -q "问题"
```

交互模式：

```bash
python main.py
```

### 5.2 原有线性实现（兼容/降级）

```bash
python main.py --engine classic -q "问题"
```

### 5.3 运行测试

在 `sql_agent` 目录下：

```bash
python -m pytest tests -v
```

测试不需要真实大模型 API Key，也不依赖真实数据库：用 `tmp_path` 创建临时 SQLite 库，用 `ScriptedLLM`/`FakeLLM` 按 Prompt 类型返回预设内容。实测结果：`20 passed`（Agent 链路 8 个 + REST 接口 12 个）。

## 6. REST 接口（FastAPI）

### 6.1 启动服务

```bash
pip install fastapi "uvicorn[standard]" httpx
$env:OPENAI_API_KEY = "sk-你的DashScopeKey"
$env:SQL_AGENT_DB_PATH = "..\04_SQL-Code-Agent-Demo\chinook.db"   # 可选
python -m uvicorn api:app --host 127.0.0.1 --port 8000
```

- Swagger 文档：<http://127.0.0.1:8000/docs>
- `SQL_AGENT_ENGINE=classic` 可切换到原有线性实现，默认 `graph`
- `SQL_AGENT_MAX_REWRITES` / `SQL_AGENT_MAX_ROWS` / `SQL_AGENT_SEMANTIC_CHECK=0` 可选
- 测试中可通过 `create_app(agent_factory=...)` 注入假 LLM 与临时数据库，模块导入阶段不会创建 Agent

### 6.2 接口总览

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/health` | 健康检查（Agent 与数据库连接状态） |
| `GET` | `/schema/tables` | 表清单、行数与字段统计 |
| `GET` | `/schema?tables=a,b` | 完整 schema 文本，可按表过滤 |
| `POST` | `/query` | 自然语言问答，返回 answer、SQL、trace、耗时 |
| `POST` | `/sql/validate` | 单条 SQL 只读/单语句/语法校验 |
| `POST` | `/sql/execute` | 单条只读 SQL 执行 |

### 6.3 错误码约定

业务异常统一返回 `{"detail": "...", "code": "..."}`；请求体字段校验失败时由 FastAPI 返回 `{"detail": [...]}`（数组）。

| HTTP 状态码 | `code` | 含义 |
| --- | --- | --- |
| `400` | `invalid_request` | 参数或 SQL 不合法（如非只读 SQL、多语句） |
| `422` | `sql_agent_error` | `SQLAgentError`：校验失败、改写重试耗尽 |
| `422` | — | 请求体校验失败（问题为空、字段缺失、`max_rows` 越界） |
| `404` | — | `/schema` 指定的表不存在 |
| `500` | `database_error` | SQLite 执行错误 |
| `502` | `llm_error` | 大模型调用/鉴权失败（`RuntimeError`） |
| `503` | — | Agent 初始化失败或数据库不可用（健康检查返回 `status=error`） |

### 6.4 健康检查 `GET /health`

```bash
curl http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "db_path": "H:\\...\\Week13\\04_SQL-Code-Agent-Demo\\chinook.db",
  "engine": "LangGraphSQLAgent",
  "table_count": 11,
  "error": null
}
```

初始化失败或数据库不可用时返回 `503`，体如 `{"status": "error", "error": "..."}`。

### 6.5 表清单 `GET /schema/tables`

```bash
curl http://127.0.0.1:8000/schema/tables
```

```json
{
  "count": 2,
  "tables": [
    {
      "name": "customers",
      "row_count": 3,
      "fields": [
        {"name": "id", "type": "INTEGER", "primary_key": true, "nullable": false,
         "distinct": 3, "nan_count": 0, "min": 1, "max": 3, "mode": 1}
      ]
    }
  ]
}
```

### 6.6 Schema `GET /schema`

```bash
curl "http://127.0.0.1:8000/schema?tables=customers,invoices"
```

```json
{
  "tables": ["customers", "invoices"],
  "schema": "### 表 customers（共 3 行）\n| 字段 | 类型 | ...\n\n### 表 invoices（共 4 行）..."
}
```

指定不存在的表返回 `404`。

### 6.7 自然语言问答 `POST /query`

```bash
curl -X POST http://127.0.0.1:8000/query \
  -H "Content-Type: application/json" \
  -d '{"question": "每位客户的总消费是多少？", "include_rows": true}'
```

请求体：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `question` | `string` | 是 | 1~2000 字符，去除空白后不能为空 |
| `include_rows` | `bool` | 否 | 默认 `true`；`false` 时 `rows` 返回空数组，仅保留行数 |

响应示例：

```json
{
  "question": "每位客户的总消费是多少？",
  "answer": "Alice 的总消费最高，为 30.5。",
  "sql": "SELECT c.name, SUM(i.total) AS total FROM customers c JOIN invoices i ON i.customer_id = c.id GROUP BY c.name ORDER BY total DESC",
  "tables": ["customers", "invoices"],
  "attempts": 1,
  "columns": ["name", "total"],
  "rows": [["Alice", 30.5], ["Bob", 30.0], ["Carol", 5.0]],
  "row_count": 3,
  "truncated": false,
  "elapsed_ms": 12.34,
  "trace": [
    {"stage": "数据库解析", "detail": "共 2 张表、1 条外键关系", "payload": {"tables": ["customers", "invoices"]}},
    {"stage": "table 选择", "detail": "选中 customers, invoices", "payload": {"tables": ["customers", "invoices"]}},
    {"stage": "SQL 生成", "detail": "SELECT ...", "payload": {"sql": "SELECT ..."}},
    {"stage": "SQL 校验", "detail": "通过", "payload": {"valid": true, "issues": "ok", "sql": "SELECT ..."}},
    {"stage": "SQL 执行", "detail": "返回 3 行", "payload": {"columns": ["name", "total"], "row_count": 3, "truncated": false}},
    {"stage": "结果汇总", "detail": "Alice 的总消费最高，为 30.5。", "payload": {"answer": "..."}}
  ]
}
```

重试耗尽等业务失败返回 `422`，例如：

```json
{"detail": "SQL 经过 3 次生成仍未通过校验：SQL 执行失败：...", "code": "sql_agent_error"}
```

### 6.8 SQL 校验 `POST /sql/validate`

```bash
curl -X POST http://127.0.0.1:8000/sql/validate \
  -H "Content-Type: application/json" \
  -d '{"sql": "DELETE FROM invoices"}'
```

```json
{"valid": false, "message": "仅允许执行 SELECT 查询", "sql": "DELETE FROM invoices"}
```

注意：SQL 本身不合法时仍返回 HTTP `200`，用 `valid`/`message` 表达结果；只有请求体字段非法才返回 `422`。

### 6.9 SQL 执行 `POST /sql/execute`

```bash
curl -X POST http://127.0.0.1:8000/sql/execute \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT id, name FROM customers ORDER BY id", "max_rows": 2}'
```

```json
{
  "sql": "SELECT id, name FROM customers ORDER BY id",
  "columns": ["id", "name"],
  "rows": [[1, "Alice"], [2, "Bob"]],
  "row_count": 2,
  "truncated": true,
  "elapsed_ms": 0.42
}
```

- 非只读/多语句/语法错误：`400`
- `max_rows` 不在 1~5000：`422`

## 7. 图结构说明

### 7.1 状态图

```
START
  ↓
load_schema
  ↓
select_tables
  ↓
generate_sql
  ↓
validate_sql ──校验通过──→ execute_sql ──执行成功──→ summarize → END
  │                          │
  │校验失败                   │执行失败
  ↓                          ↓
  └──────→ rewrite_sql ←─────┘
              ↓
          validate_sql（循环）
              ↓ 改写次数用尽
            fail → END
```

### 7.2 节点职责

| 节点 | 职责 |
| --- | --- |
| `load_schema` | 记录数据库解析结果（表数量、外键数量） |
| `select_tables` | 让模型选表，失败时回退到关键词匹配 |
| `generate_sql` | 基于选中表生成 SQL，并重置重试计数 |
| `validate_sql` | 本地只读/语法校验 + 可选 LLM 语义校验 |
| `execute_sql` | 执行只读 SQL，捕获执行异常 |
| `rewrite_sql` | 根据校验/执行问题改写 SQL，`attempts + 1` |
| `summarize` | 查询结果转自然语言；空结果直接返回固定文案 |
| `fail` | 重试额度用尽，写入错误信息，图结束后抛出 `SQLAgentError` |

### 7.3 状态字段（`AgentState`）

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `question` | `str` | 用户问题 |
| `tables` | `list[str]` | 选中的表 |
| `sql` | `str` | 当前 SQL |
| `attempts` | `int` | 当前 SQL 的生成次数（初始为 1，每次改写 +1） |
| `valid` | `bool` | 最近一次校验结果 |
| `issues` | `str` | 最近一次失败原因 |
| `exec_result` | `dict \| None` | 执行结果（columns/rows/row_count/truncated） |
| `answer` | `str` | 最终回答 |
| `error` | `str \| None` | 非空表示工作流失败 |
| `trace` | `list[TraceStep]` | 过程记录，使用 reducer 追加 |

### 7.4 条件路由与重试规则

- `validate_sql`：通过 → `execute_sql`；不通过 → `rewrite_sql`；`attempts - 1 >= max_rewrite_rounds` 时 → `fail`。
- `execute_sql`：有结果 → `summarize`；异常 → `rewrite_sql`；无改写额度时 → `fail`。
- 总生成次数为 `max_rewrite_rounds + 1`（1 次初始生成 + N 次改写）。
- 每次 `graph.invoke` 的 `recursion_limit` 按 `max(25, 3 * max_rewrite_rounds + 10)` 设置，避免大重试次数触发误报。

### 7.5 对外接口

`LangGraphSQLAgent` 继承 `SQLAgent`，构造参数与 `ask()` 返回结构完全一致，可直接替换：

```python
from langgraph_agent import LangGraphSQLAgent

with LangGraphSQLAgent(
    db_path="path/to/chinook.db",
    max_rewrite_rounds=2,
    max_rows=200,
    semantic_check=True,
) as agent:
    result = agent.ask("每位客户的总消费是多少？")
    print(result.sql)
    print(result.answer)
    for step in result.trace:
        print(step.stage, step.detail)
```

编译后的图可通过 `agent.graph` 访问，例如 `agent.graph.get_graph().nodes` 查看节点。

## 8. 常见问题

**Q1：启动报「未配置大模型 API Key」**
配置环境变量 `OPENAI_API_KEY`；若只验证图结构/流程，可直接运行 `python -m pytest tests -v`（无需 Key）。

**Q2：启动报「数据库文件不存在」**
仓库未附带 `chinook.db`。请把数据库放到 `Week13/04_SQL-Code-Agent-Demo/chinook.db`，或使用 `--db 你的路径.db`。

**Q3：报 `缺少依赖 ... langgraph`**
执行 `pip install langgraph`；或临时用 `python main.py --engine classic` 运行原实现。

**Q4：提示 SQL 重试次数用尽（`SQLAgentError`）**
适当增大 `--max-rewrites`，或先用 `--trace` 查看每轮校验失败原因；也可用 `--no-semantic-check` 排除语义校验误判。

**Q5：语义校验多花一次模型调用**
语义校验本身是额外的 LLM 调用，可用 `--no-semantic-check` 关闭；原逻辑中语义校验调用异常会自动跳过，不会中断流程。

**Q6：结果被截断**
默认最多 200 行，可用 `--max-rows` 调整；`AgentResult.truncated` 会标记是否截断。

**Q7：`/health` 返回 503**
表示 Agent 初始化失败（常见为缺少 `OPENAI_API_KEY` 或数据库路径不存在）或数据库连接不可用；响应中的 `error` 字段给出具体原因。

**Q8：`/query` 返回 422 且 `code=sql_agent_error`**
表示业务执行失败：SQL 多轮改写后仍未通过校验/执行，可查看 `detail`，并调整 `SQL_AGENT_MAX_REWRITES` 或换更明确的问题。

**Q9：`/sql/execute` 返回 400**
表示 SQL 不是单条只读查询（含 INSERT/UPDATE/DELETE/DDL、多语句或语法错误）；先用 `/sql/validate` 排查。

## 9. 限制与未完成项

- 仓库未附带 `chinook.db`，主程序与 API 需自备数据库文件。
- 本环境未配置真实 DashScope Key，**未对真实大模型调用做端到端验证**；测试使用 `ScriptedLLM` / `FakeLLM` 假客户端与临时 SQLite 库，覆盖 Agent 正常链路、校验失败重试、语义校验拒绝、执行异常、重试耗尽、空结果、空问题、原有实现回归，以及 REST 接口的成功/参数非法/业务异常/健康检查。
- REST 服务在进程内共享同一个 Agent 与 SQLite 连接；接口使用 `async def` 且内部为同步调用，长查询会阻塞事件循环。未实现连接池、鉴权、限流、并发隔离，生产部署需进一步改造。
- 未实现流式输出、LangGraph checkpoint/持久化、人工审核中断（human-in-the-loop）、任务队列等高级能力；仅使用 `StateGraph` 的节点、条件边与循环。
- 依赖版本为实测环境版本，其他版本未逐一验证；LangGraph 1.x 的 `StateGraph / add_conditional_edges / compile()` API 与实现一致。
- API 测试基于 FastAPI `TestClient`，未做多进程/反向代理/压力测试。
