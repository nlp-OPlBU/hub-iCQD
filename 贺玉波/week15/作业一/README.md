# week15 作业1 · 情感识别 → 鼓励 / 排解

同一套业务逻辑，分别用 **LangGraph** 和 **OpenAI Agents SDK** 实现，并按“进程1 / 进程2 / 后端 / 前端”拆成可独立启动的进程。

> 作业2（Dockerfile 打包）在 `../作业二/`，基于本目录的 `openai_agents_version` 代码。

## 一、作业对应

| 作业要求 | LangGraph 版 | openai-agents 版 |
|---|---|---|
| 进程1：接受请求进行情感识别 | `sentiment_service.py`（:8101），单节点图 `classify` | `sentiment_service.py`（:8201），`sentiment_agent` |
| 进程2：正向鼓励、负面排解 | `response_service.py`（:8102），条件边路由到 `encourage` / `soothe` / `neutral` | `response_service.py`（:8202），三个 Agent + 确定性选择 |
| 后端进程：串联进程1和进程2 | `backend.py`（:8000），`POST /api/analyze` | `backend.py`（:8200），同一契约 |
| 前端进程：用户输入 | `static/index.html` 网页 + `cli.py` 命令行 | 同左 |
| 分别使用两个框架 | `langgraph>=1.0` | `openai-agents>=0.20` |

进程1、进程2、后端是 **三个真实的 OS 进程**，通过 HTTP 通信；前端进程是网页（由后端托管）或独立的 `cli.py`。

## 二、架构

```
浏览器 / cli.py（前端进程）
        │  POST /api/analyze {"text": "..."}
        ▼
后端进程 :8000 / :8200
        │  POST /classify  {"text": "..."}
        ▼
进程1 情感识别 :8101 / :8201   →  {sentiment, confidence, reasoning}
        │
        │  POST /respond  {"text": "...", "sentiment": "..."}
        ▼
进程2 鼓励/排解 :8102 / :8202  →  {reply}
```

- LangGraph 版默认端口：`8000 / 8101 / 8102`
- openai-agents 版默认端口：`8200 / 8201 / 8202`（两套可以同时运行）

## 三、目录

```
week15/作业一/
├── README.md
├── langgraph_version/
│   ├── sentiment_service.py   # 进程1：LangGraph 单节点图
│   ├── response_service.py    # 进程2：LangGraph 条件边
│   ├── backend.py             # 后端：串联 + 托管网页
│   ├── cli.py                 # 前端 CLI 进程
│   ├── static/index.html      # 前端网页
│   ├── llm.py                 # DeepSeek 调用 + MOCK
│   ├── schemas.py             # 数据契约
│   ├── config.py              # 环境变量配置
│   ├── run_all.py             # 一条命令拉起三个进程
│   ├── requirements.txt
│   └── .env.example
└── openai_agents_version/
    ├── sentiment_service.py   # 进程1：sentiment_agent
    ├── response_service.py    # 进程2：encourager / soother / neutral Agent
    ├── agent_runtime.py       # Agents SDK 运行时 + MOCK
    ├── backend.py / cli.py / static/index.html / schemas.py / config.py / run_all.py
    ├── requirements.txt
    └── .env.example
```

## 四、启动

### 1. 安装依赖

```powershell
cd 贺玉波\week15\作业一\langgraph_version
pip install -r requirements.txt

cd ..\openai_agents_version
pip install -r requirements.txt
```

### 2. 配置模型

```powershell
copy .env.example .env
```

编辑 `.env`：

```ini
DEEPSEEK_API_KEY=sk-你的key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

不配置 key 时自动进入本地 MOCK 模式（关键词规则 + 模板回复），也能完整演示三进程链路；也可以显式设置 `MOCK=1`。

### 3. 启动三个进程

```powershell
# LangGraph 版
cd 贺玉波\week15\作业一\langgraph_version
python run_all.py

# openai-agents 版（另开一个终端）
cd 贺玉波\week15\作业一\openai_agents_version
python run_all.py
```

然后打开：

- LangGraph 版：<http://127.0.0.1:8000>
- openai-agents 版：<http://127.0.0.1:8200>

命令行前端（另开终端）：

```powershell
python cli.py
```

也可以手动开三个终端，分别启动：

```powershell
uvicorn sentiment_service:app --port 8101
uvicorn response_service:app --port 8102
uvicorn backend:app --port 8000
```

## 五、接口

| 进程 | 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|---|
| 进程1 | POST | `/classify` | `{"text": "..."}` | `{"sentiment": "positive/negative/neutral", "confidence": 0.99, "reasoning": "..."}` |
| 进程2 | POST | `/respond` | `{"text": "...", "sentiment": "negative"}` | `{"reply": "..."}` |
| 后端 | POST | `/api/analyze` | `{"text": "..."}` | `{text, sentiment, confidence, reasoning, response_text, provider, elapsed_ms}` |
| 后端 | GET | `/health` | — | 三个进程的聚合健康状态 |

## 六、两套实现的差异

| 维度 | LangGraph 版 | openai-agents 版 |
|---|---|---|
| 进程1 | `StateGraph` + `classify` 节点 | `Agent` + `Runner.run` |
| 进程2 路由 | `add_conditional_edges`，图结构可见 | `dict[Sentiment, Agent]` + 代码选择，不使用 handoff |
| 结构化输出 | `response_format={"type":"json_object"}` + Pydantic 校验 | 提示词要求 JSON + Pydantic 校验 |
| 状态 | `TypedDict`，纯内存 | 局部变量 |
| 生成温度 | 分类 0，回复 0.7 | 同左（`ModelSettings`） |

**DeepSeek 兼容性结论（已实测）**：Agents SDK 的 `Agent(output_type=...)` 会以 `response_format=json_schema` 调用接口，DeepSeek 返回 `400 This response_format type is unavailable now`。因此 openai-agents 版不用 `output_type`，改为“文本 JSON + 校验 + 失败重试一次 + 降级中性”，并在 `agent_runtime.py` 注释说明。`OpenAIChatCompletionsModel` + `AsyncOpenAI(base_url=...)` 是必须的，否则会请求 `/responses`。

## 七、验收记录

以下结果均在本机实际跑通：

| 检查 | 结果 |
|---|---|
| LangGraph 版三进程启动，`/health` 聚合为 `ok` | 通过 |
| openai-agents 版三进程启动，`/health` 聚合为 `ok` | 通过 |
| MOCK 模式：正向 / 负面 / 中性三类输入 | 通过（36ms / 7ms / 6ms） |
| DeepSeek 真实调用：LangGraph 版负面输入 | 通过（6014ms，置信度 0.97） |
| DeepSeek 真实调用：openai-agents 版正向输入 | 通过（7028ms，置信度 0.99） |
| 网页端到端：输入 → 点击“开始分析” → 正向卡片 + 非空回应 | 通过（浏览器实测，无控制台报错，`POST /api/analyze` 200） |
| CLI 端到端 | 通过（输入后打印情感、置信度、理由、回应、耗时） |
| 后端未启动时 CLI 的报错 | 通过（提示连接失败，不抛 traceback） |

## 八、已知限制

- MOCK 模式是关键词规则，只用于演示链路，不代表真实模型效果。
- 对话是单轮的，不保存上下文；每次请求独立。
- 回复由模型生成，属于情绪陪伴演示，不构成心理诊断或治疗建议。
- 本轮只交付作业1；作业2（Dockerfile / 镜像打包）见 `../作业二/`。
