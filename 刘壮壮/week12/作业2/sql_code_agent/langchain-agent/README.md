# SQL Code Agent（LangChain）

一个面向 SQLite 的只读自然语言问答 Agent。项目使用 LangChain 调用大语言模型并约束结构化输出，使用 LangGraph 编排选表、SQL 生成、校验、改写、执行和答案汇总流程。

当前示例数据库为 [Chinook](https://github.com/lerocha/chinook-database)，支持使用中文问题查询其中的音乐商店数据。

## 功能特点

- 将中文自然语言问题转换为 SQLite 查询。
- 自动分析数据库 Schema，并只向模型提供相关表结构。
- 使用 Pydantic 校验选表、SQL 草稿和最终答案等模型输出。
- SQL 执行失败时，在有限次数内自动改写并重新校验。
- 支持 DeepSeek 和 OpenAI 两种模型 Provider。
- 联系方式等敏感样本字段在发送给模型前自动脱敏。
- 普通单元测试可使用 Fake Gateway，无需调用真实模型 API。

## 工作流程

```text
用户问题
   ↓
解析数据库 Schema
   ↓
选择相关数据表
   ↓
生成 SQL
   ↓
安全校验 ──危险请求──→ 拒绝
   ↓
执行 SQL ──可修复错误──→ 改写 SQL ──→ 重新校验
   ↓
汇总查询结果
   ↓
中文答案
```

LangGraph 使用 `StateGraph` 定义节点和条件路由，图在调用前通过 `compile()` 编译，并通过异步 `ainvoke()` 执行。每次问答拥有独立状态，不保存跨请求记忆。

## 安全设计

本项目只允许读取数据库。安全边界由本地 Python 和 SQLite 控制，不依赖模型自觉遵守 Prompt：

- 原始问题中的创建、修改、删除和导出意图会被拒绝。
- 仅允许单条 `SELECT` 或只读 `WITH` 语句。
- 静态拦截 DML、DDL、`PRAGMA`、`ATTACH`、`VACUUM` 等操作。
- 执行前使用 `EXPLAIN QUERY PLAN` 进行语法和 Schema 预检。
- 预检与正式执行均使用 SQLite `mode=ro`、`query_only` 和 authorizer。
- 通过执行步数限制防止失控查询，通过结果行数限制控制返回规模。
- SQL 改写后必须再次通过完整安全校验。

> 该实现适合教学和实验用途。若用于生产环境，还需要结合身份认证、审计、数据权限和部署环境进行额外加固。

## 环境要求

- Python 3.12 或更高版本
- SQLite 数据库
- DeepSeek 或 OpenAI API Key

默认数据库路径为仓库根目录下的 `data/chinook.db`，即：

```text
sql_code_agent/
├── data/
│   └── chinook.db
└── langchain-agent/
```

如果数据库位于其他位置，请通过 `SQL_AGENT_DB_PATH` 指定。

## 安装

项目使用名为 `py312` 的 Conda 环境。激活环境后安装项目及开发依赖：

```bash
conda activate py312
cd langchain-agent
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
```

## 配置

复制环境变量模板：

```bash
cp .env.example .env
```

### 使用 DeepSeek

编辑 `.env`：

```dotenv
SQL_AGENT_PROVIDER=deepseek
SQL_AGENT_MODEL=deepseek-chat
DEEPSEEK_API_KEY=your_api_key
SQL_AGENT_BASE_URL=https://api.deepseek.com
```

### 使用 OpenAI

编辑 `.env`：

```dotenv
SQL_AGENT_PROVIDER=openai
SQL_AGENT_MODEL=your_model_name
OPENAI_API_KEY=your_api_key
```

### 可选配置

- `SQL_AGENT_DB_PATH`：数据库路径；留空时使用 `../data/chinook.db`。
- `SQL_AGENT_MAX_REWRITES`：SQL 最大改写次数，默认为 `2`，允许范围为 `0–5`。
- `SQL_AGENT_MAX_RESULT_ROWS`：最多返回的结果行数，默认为 `100`。
- `SQL_AGENT_MAX_PROGRESS_STEPS`：SQLite 查询执行步数上限，默认为 `1000000`。
- `SQL_AGENT_MODEL_TIMEOUT_SECONDS`：单次模型请求超时秒数，默认为 `60`。
- `SQL_AGENT_MODEL_MAX_RETRIES`：模型网络请求重试次数，默认为 `1`。

### 启用 LangSmith

直接使用 LangSmith 官方环境变量，项目不做额外封装：

```dotenv
LANGSMITH_TRACING=true
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
LANGSMITH_API_KEY=your_langsmith_api_key
LANGSMITH_AGENT_ID=sql-agent-lc
LANGSMITH_AGENT_ENVIRONMENT=local
```

- `LANGSMITH_TRACING` 未设置或为 `false` 时不会上报任何数据。开启后用户问题、Schema 样本和查询结果都会发送到 LangSmith，请先确认数据治理要求。
- `LANGSMITH_AGENT_ID` 和 `LANGSMITH_AGENT_ENVIRONMENT` 必须成对设置，且不要同时设置 `LANGSMITH_PROJECT`，否则 SDK 会因目标冲突而不上报 trace。
- 需要 `langsmith>=0.14.14`。

每次请求在 LangSmith 中是一条名为 `sql_agent_workflow` 的根 trace，metadata 包含 `request_id`、`provider` 和 `model`；各模型调用以子 run 形式挂在其下，并带有 `prompt_version`。

不要提交 `.env` 或在日志中输出 API Key。

## 命令行使用

安装后会注册 `sql-agent-lc` 命令：

```bash
sql-agent-lc "销量最高的五位艺术家是谁？"
```

不带参数运行时会提示交互输入问题：

```bash
sql-agent-lc
请输入问题：每个国家有多少位客户？
```

添加 `--verbose` 可输出每个阶段的结构化 JSON 日志（选表、SQL 生成、校验、执行、改写、汇总等），便于排查：

```bash
sql-agent-lc --verbose "2013 年总销售额是多少？"
```

退出码约定：

| 退出码 | 含义 |
| --- | --- |
| `0` | 查询成功，或危险请求已被安全拒绝 |
| `1` | 工作流失败，如无法选表、改写次数耗尽、查询超出资源限制 |
| `2` | 问题为空、配置错误或模型输出不符合结构化契约 |
| `130` | 用户按 `Ctrl+C` 取消 |

失败时只向标准错误输出稳定的中文提示，不暴露 API Key 或 Provider 原始响应。

## Python API

也可以在自己的异步应用中直接使用：

```python
from langchain_sql_agent.config import Settings
from langchain_sql_agent.graph import SqlAgentGraph

agent = SqlAgentGraph(Settings.from_env())
result = await agent.answer("每个国家有多少位客户？")
print(result.answer)
```

## 测试

运行全部单元测试：

```bash
python -m pytest
```

测试覆盖以下核心行为：

- 配置读取和数据库路径校验
- Schema 解析、缓存与样本脱敏
- SQL 静态检查、编译预检和危险语句拦截
- 只读执行、结果截断与资源限制
- LangGraph 正常路由、选表重试、SQL 改写和安全短路

普通测试通过 Fake Gateway 隔离模型调用，因此不需要 API Key，也不会产生模型调用费用。

## 项目结构

```text
langchain-agent/
├── src/langchain_sql_agent/
│   ├── chains.py          # LangChain 结构化模型链
│   ├── cli.py             # 命令行入口、日志初始化与退出码
│   ├── config.py          # 环境变量与运行配置
│   ├── graph.py           # LangGraph 状态、节点与路由
│   ├── models.py          # Pydantic 数据契约
│   ├── query_executor.py  # 只读 SQL 执行器
│   ├── schema_service.py  # Schema 解析、缓存与脱敏
│   └── sql_safety.py      # SQL 安全校验
├── tests/                 # 单元测试
├── .env.example           # 环境变量模板
├── pyproject.toml         # 项目元数据与依赖
└── 技术实现文档.md         # 详细设计说明
```

## 核心技术

- [LangChain](https://python.langchain.com/)：Prompt、模型适配和结构化输出
- [LangGraph](https://docs.langchain.com/oss/python/langgraph/overview)：显式状态图和条件路由
- [Pydantic](https://docs.pydantic.dev/)：配置与阶段数据契约
- SQLite：Schema 解析、只读预检和查询执行
- pytest / pytest-asyncio：同步与异步测试

更完整的架构、安全策略和测试方案请参阅[技术实现文档](./技术实现文档.md)。
