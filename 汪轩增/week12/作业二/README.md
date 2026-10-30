# SQL Agent（langchain 复刻版）

把 `task1/00-sql-agent/` 中用 **openai-agents** 实现的 Chinook SQL Agent，
用 **langchain** 复刻一遍。业务逻辑、数据契约、安全门禁、重试预算完全不变，
只替换模型接入层。

## 复刻了什么

| openai-agents 版 | langchain 版 |
| --- | --- |
| `Agent(model=..., output_type=X)` | `ChatOpenAI(...).with_structured_output(X)`（见 `llm.py`） |
| `await Runner.run(agent, prompt).final_output` | `await invoke_structured(llm, system_prompt, prompt)` |
| `agents/__init__.py` 里 `set_default_openai_api(...)` 等 SDK 配置 | `llm.py` 统一构造 `ChatOpenAI` |
| `with trace("SQLAgentWorkflow")` 追踪 | 去掉 trace，其余日志不变 |

**未改动**（与框架无关的确定性代码，直接复用）：
`models.py`（Pydantic 契约）、`config.py`、`db/catalog.py`（schema 解析）、
`db/validator.py`（SQL 安全校验）、`db/executor.py`（只读执行）、
`logging_config.py`、`cli.py`。

## 工作流

```
用户问题 → 表选择 → SQL 生成 → SQL 校验 → SQL 执行 → 结果汇总
                          ↑            │
                          └── 改写修复（最多 3 次）──┘
```

- 四个 LLM 步骤：表选择 / SQL 生成 / SQL 改写 / 结果汇总（`agents/`）
- 两个确定性步骤：校验与执行（`db/`，只读、超时、截断）
- 编排用 Python 代码（`workflow.py`），不使用 langchain 的 Agent handoff

## 目录结构

```
作业二/
├── README.md
├── requirements.txt
├── ref/chinook.db              # 测试数据库（SQLite，只读）
└── src/sql_agent/
    ├── __main__.py             # python -m sql_agent 入口
    ├── cli.py
    ├── config.py               # 模型 / 重试 / 路径配置
    ├── llm.py                  # 【新增】langchain ChatOpenAI + 结构化输出
    ├── logging_config.py
    ├── models.py               # Pydantic 契约
    ├── workflow.py             # 【改写】编排（Runner.run → ainvoke）
    ├── agents/                 # 【改写】4 个 LLM Agent
    │   ├── table_selector.py
    │   ├── sql_generator.py
    │   ├── sql_rewriter.py
    │   └── result_summarizer.py
    └── db/                     # 确定性组件（复用）
        ├── catalog.py
        ├── validator.py
        └── executor.py
```

## 运行

1. 安装依赖：

   ```bash
   pip install -r requirements.txt
   ```

2. 配置 API Key（langchain 的 `ChatOpenAI` 自动读取 `OPENAI_API_KEY`）：

   ```bash
   export OPENAI_API_KEY=sk-xxxx
   # 默认 base_url 为 https://api.deepseek.com（deepseek-flash），
   # 也可换成 DashScope：export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
   ```

3. 运行（从 `作业二/` 目录）：

   ```bash
   PYTHONPATH=src python -m sql_agent "总共有多少张专辑？"
   ```

   最终 JSON 输出到 stdout，日志输出到 stderr。

   其他问题示例：

   ```bash
   PYTHONPATH=src python -m sql_agent "每个艺人有几张专辑？按数量从高到低排序"
   PYTHONPATH=src python -m sql_agent "2013 年销售额最高的客户是谁？"
   PYTHONPATH=src python -m sql_agent "帮我查一下今天北京天气"   # 无关问题 → 澄清/拒绝
   ```

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `OPENAI_API_KEY` | — | 必填 |
| `OPENAI_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容端点 |
| `SQL_AGENT_MODEL` | `deepseek-flash` | 选表/生成/改写用模型 |
| `SQL_AGENT_SUMMARY_MODEL` | `deepseek-flash` | 汇总用模型 |
| `SQL_AGENT_MAX_REPAIR_ATTEMPTS` | `3` | 修复次数上限 |
| `SQL_AGENT_DB` | `ref/chinook.db` | 数据库路径 |
| `SQL_AGENT_LOG_LEVEL` | `INFO` | 日志级别 |

> 换用 qwen：`export OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1` 并把
> `SQL_AGENT_MODEL` / `SQL_AGENT_SUMMARY_MODEL` 设为 `qwen-max` / `qwen-plus`。
