# Week13 作业二 · LangGraph SQL Agent + 执行过程可视化前端

在作业一的 LangGraph agent 基础上增加 **FastAPI 后端** 和 **单页前端**：每次问答不仅返回最终答案，还把命中的节点、选中的表、SQL、校验结果、行数、修复次数一并回传，前端按步骤展开展示。

## 1. 架构

```
web/index.html + style.css + app.js
            │  POST /api/chat          POST /api/chat/resume
            ▼                                │
server/main.py (FastAPI) ── 共享 MemorySaver ◀┘
            │  复用同一个
            ▼
sql_agent_lg  ← CLI(run.py) 也直接调用它
   └── graph.py (LangGraph StateGraph + checkpointer)
```

- `sql_agent_lg` **只有一份**：CLI 与 API 都调用 `SQLAgentGraph.start_run()` / `resume_run()`，没有复制编排逻辑。
- API 额外返回 `trace`（节点轨迹）和 `result_table`（结果集），CLI 仍保持 week12 的 JSON 契约。
- **Human-in-the-loop**：`clarify` 节点用 `interrupt()` 暂停；`/api/chat` 返回
  `status=interrupted` + `thread_id` + 澄清问题/选项，`/api/chat/resume` 用
  `Command(resume=answer)` 从同一检查点继续，不重复选表。
- 没有 API key 时：后端健康检查返回 `key_configured=false`，前端自动打开「离线演示」；也可以直接用请求参数 `mock=true`。

## 2. 启动方式

在 `week13/作业二` 目录下：

```powershell
conda activate study
pip install -r requirements.txt

# 启动后端（同时托管 web 静态页面）
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

浏览器打开：

```
http://127.0.0.1:8000/
```

不填 `.env` 也能看完整效果：页面检测到无 key 会自动勾选「离线演示」，此时真实执行 `data/chinook.db`，Mock 只替代模型决策。

想直接演示 HITL 澄清卡片，可让 Mock 每次都触发澄清（仅用于演示）：

```powershell
$env:SQL_AGENT_MOCK_SCENARIO="clarify"
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

真实模型模式：

```powershell
copy .env.example .env
# 编辑 .env，填入 DEEPSEEK_API_KEY
python -m uvicorn server.main:app --host 127.0.0.1 --port 8000
```

## 3. API

### `GET /api/health`

```json
{
  "ok": true,
  "model": "deepseek-flash",
  "db": "chinook.db",
  "db_exists": true,
  "key_configured": false,
  "mock_default": false,
  "graph_nodes": ["select_tables", "generate_sql", "validate_sql",
                  "execute_sql", "repair_sql", "summarize", "clarify", "fail"],
  "hitl": {"enabled": true, "node": "clarify", "resume_endpoint": "/api/chat/resume"}
}
```

### `POST /api/chat`

请求：

```json
{
  "question": "总共有多少张专辑？",
  "history": [{"question": "上一轮问题", "answer": "上一轮答案"}],
  "mock": true,
  "max_repair_attempts": 3
}
```

`sql` / `tables` 可选：传 `sql` 可跳过选表与生成节点，直接验证校验、执行、汇总链路。

响应（节选）：

```json
{
  "ok": true,
  "status": "completed",
  "thread_id": "0c1e…",
  "interrupt": null,
  "result": {
    "question": "总共有多少张专辑？",
    "answer": "查询结果：album_count = 347。",
    "sql": "SELECT COUNT(*) AS album_count FROM albums",
    "tables": ["albums"],
    "row_count": 1,
    "truncated": false,
    "repair_attempts": 0,
    "evidence": ["album_count = 347"],
    "caveats": ["Mock 模式：答案由确定性代码生成，未调用外部模型。"],
    "error": null
  },
  "trace": [
    {"node": "select_tables", "title": "选择数据表", "status": "ok",
     "elapsed_ms": 0, "detail": {"tables": ["albums"], "confidence": 0.9}},
    {"node": "generate_sql", "title": "生成 SQL", "status": "ok",
     "detail": {"sql": "SELECT COUNT(*) AS album_count FROM albums;"}},
    {"node": "validate_sql", "title": "校验 SQL", "status": "ok",
     "detail": {"ok": true, "code": null, "message": ""}},
    {"node": "execute_sql", "title": "执行 SQL", "status": "ok",
     "detail": {"ok": true, "row_count": 1, "truncated": false}},
    {"node": "summarize", "title": "汇总答案", "status": "ok",
     "detail": {"answer": "查询结果：album_count = 347。", "provider": "mock"}}
  ],
  "result_table": {
    "columns": ["album_count"],
    "rows": [["347"]],
    "row_count": 1,
    "truncated": false
  },
  "meta": {"provider": "mock", "model": "deepseek-flash", "elapsed_ms": 1855}
}
```

命中澄清时（HTTP 仍为 `200`，不是错误）。下例只是示意，澄清问题与选项由模型生成，不同运行可能不同：

```json
{
  "ok": true,
  "status": "interrupted",
  "thread_id": "0c1e…",
  "interrupt": {
    "node": "clarify",
    "question": "请问要统计哪位客户的总消费？请提供客户姓名或 CustomerId。",
    "options": ["按 CustomerId 指定客户", "按姓名（FirstName/LastName）指定客户", "按邮箱 Email 指定客户"]
  },
  "result": {
    "question": "某个客户的总消费是多少？",
    "answer": "请问要统计哪位客户的总消费？请提供客户姓名或 CustomerId。",
    "tables": ["invoices", "customers"],
    "row_count": 0,
    "error": null
  },
  "trace": [
    {"node": "select_tables", "status": "warn", "detail": {"needs_clarification": true}},
    {"node": "clarify", "title": "等待人工澄清", "status": "warn", "detail": {"waiting": true}}
  ]
}
```

### `POST /api/chat/resume`

请求：

```json
{"thread_id": "0c1e…", "answer": "查询全部客户各自的总消费", "mock": false}
```

- 成功：返回 `status=completed`，`trace` 包含 `clarify`（人工澄清）和后续所有节点，
  `result` 为最终答案；检查点随后被清理。
- `thread_id` 不存在或已完成：`404`；`answer` 为空：`400`；未配置 key 且非 mock：`503`。

错误统一为：

```json
{"ok": false, "error": {"code": "http_503", "message": "后端未配置 DEEPSEEK_API_KEY……"}}
```

## 4. 前端功能

- 中文对话界面，支持连续多轮提问（最近 3 轮作为上下文传给后端）。
- 每条回答展示：
  - 最终答案 + 状态徽章（完成 / 未完成、Mock/DeepSeek、表、行数、修复次数、耗时）
  - **执行步骤时间线**：节点名称、状态、耗时、摘要；可展开查看节点详情 JSON 与 SQL
  - 可复制的最终 SQL
  - 可展开的结果表（最多展示 60 行，服务端最多 200 行）
  - `caveats` 提示（Mock、结果截断、汇总降级等）
- **HITL 澄清卡片**：图在 `clarify` 中断时，回答区显示澄清问题、2-3 个口径选项和自由输入框；
  点选或提交后原地调用 `/api/chat/resume`，加载态提示「已收到澄清，正在继续执行后续节点…」，
  最终替换为完整答案卡片（时间线里包含「人工澄清」步骤）。恢复失败时提供重试按钮。
- 加载态：发送后显示「正在执行 LangGraph 节点…」并禁用按钮。
- 错误态：红色错误卡片 + 重试按钮；后端未配置 key 时自动切到 Mock。
- 左右分栏布局：左侧栏放品牌、连接状态、离线演示开关、示例问题、8 节点图清单和清空按钮；右侧为对话工作区，头部与输入区紧凑，聊天区占满剩余高度。窄屏（≤980px）自动改为上下堆叠。

## 5. 示例问题

- 总共有多少张专辑？
- 销售额最高的前 10 笔发票是哪些？
- 列出前 10 位客户及其所在国家
- 最长的 10 首歌曲是哪些？

## 6. 本地验证记录（2026-11-04）

环境：`A:\conda_envs\study`，`langgraph 1.2.9`、`fastapi 0.138.0`、`uvicorn 0.52.4`。

- 后端启动：`python -m uvicorn server.main:app --host 127.0.0.1 --port 8013`
- `GET /api/health` → `200`，`db_exists=true`，`key_configured=false`
- `POST /api/chat`（`mock=true`，问题「总共有多少张专辑？」）→ `200`
  - `answer = "查询结果：album_count = 347。"`，`row_count = 1`
  - `trace` 共 5 个节点，`repair_attempts = 0`
  - `result_table.rows = [["347"]]`
- `GET /` → `200`，`2360 bytes`（静态页面正常返回）
- 多表问题（mock）：「销售额最高的前 10 笔发票是哪些？」→ `row_count = 10`，选中 `invoices + customers`
- CLI 复用验证：`python run.py --mock "列出前 10 位客户及其所在国家"` → 10 行，首行 `CustomerId=1, Country=Brazil`
- **真实 DeepSeek 调用（API）**：`mock=false`
  - `GET /api/health` → `key_configured=true`，`model=deepseek-flash`
  - `POST /api/chat` → `200`，`provider=deepseek`，`trace` 5 个节点
  - `answer = "总共有 347 张专辑。"`，`row_count=1`，`repair_attempts=0`
  - 服务端日志：3 次 `POST https://api.deepseek.com/chat/completions` 均为 `200 OK`，总耗时 7.79s
- 左右布局浏览器验证：左侧栏含 4 个示例与 8 节点清单，右侧聊天区完整展示 5 个步骤和答案 347，无水平滚动条，头部/底部无大面积空白

### HITL 验证（2026-11-04，Python 3.12 / langgraph 1.0.10）

- **Mock HTTP 中断**：`POST /api/chat`（`mock=true`，`SQL_AGENT_MOCK_SCENARIO=clarify`）
  → `status=interrupted`，`thread_id` 正常返回，`interrupt.options` 有 2 项，
  `trace` 恰为 2 个节点（`select_tables` + `clarify` 等待），HTTP 仍为 `200`。
- **Mock HTTP 恢复**：`POST /api/chat/resume`（`answer` 取选项原文）
  → `status=completed`，`result.row_count=10`，`trace` 6 个节点
  （`select_tables → clarify → generate_sql → validate_sql → execute_sql → summarize`），
  `clarify.detail.answer` 记录人工回答，检查点随后清理。
- **参数校验**：不存在的 `thread_id` → `404`；空 `answer` → `400`。
- **真实模型 HTTP**：`mock=false`，问题「某个客户的总消费是多少？」
  → `status=interrupted`，模型给出澄清问题与 3 个选项（本轮为按 CustomerId / 按姓名 / 按邮箱，
  措辞每次可能不同）；
  resume 用自由输入「查询全部客户各自的总消费」→ `completed`，`row_count=59`，`error=null`，
  `trace` 6 个节点（`select_tables → clarify → generate_sql → validate_sql → execute_sql → summarize`）。
- **真实模型 CLI**（作业二 `run.py` 同步）：`--answer "查询全部客户各自的总消费"`
  → 59 行，`repair_attempts=0`，答案指出 Helena Holý 总消费最高（49.62）。
- **浏览器验证**：打开页面并开启「离线演示」→ 发送问题 → 出现「需要补充信息 / 等待澄清」
  卡片与选项按钮 → 点击第一个选项 → 卡片替换为「回答」，时间线含「人工澄清」步骤，
  结果表正常渲染（10 行）。无水平滚动、无卡死。

> 真实模型调用已在本地 `.env`（`DEEPSEEK_API_KEY` + `SQL_AGENT_MODEL=deepseek-flash`）下验证通过。`.env` 已加入 `.gitignore`，不会提交到仓库。复现方式：按第 2 节启动后，在页面关闭「离线演示」即可。

## 7. 目录结构

```
作业二/
├── run.py                  # CLI：复用同一份 agent
├── requirements.txt
├── .env.example
├── data/chinook.db
├── README.md
├── sql_agent_lg/           # 与作业一相同的 LangGraph agent
├── server/
│   ├── __init__.py
│   └── main.py             # FastAPI：/api/health、/api/chat、静态托管
└── web/
    ├── index.html
    ├── style.css
    └── app.js
```

## 8. 已知限制

- 对话历史只保留最近 3 轮，且仅作为 LLM 提示上下文；后端不持久化会话。
- Mock 模式是固定规则模板，用于演示图流转，不代表真实模型的泛化能力。
- 结果表最多回传 200 行（执行器上限），页面最多渲染 60 行。
- 仅支持 SQLite 只读查询；写操作会在校验节点被拒绝。
- HITL 检查点放在进程内 `MemorySaver`：服务重启后未完成的澄清会话不可恢复；
  单机演示足够，生产应换成 Redis / SQLite checkpointer。
- 澄清只做一轮：用户回答后直接生成 SQL，如果仍有歧义不会再次追问。
- langgraph 1.0.10 恢复检查点时会对 state 内自定义 Pydantic 类型（`TableSelection`）
  打印 pending deprecation 警告；当前不影响恢复结果，升级到更严格版本时需把这些类
  加入 serde allowlist。
