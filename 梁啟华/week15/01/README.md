# Week 15 · 双框架情感支持应用

同一套「情感识别 → 策略回复」业务，分别用 **LangGraph** 与 **OpenAI Agents SDK** 实现为两个独立的全栈项目。两者共用 DeepSeek（OpenAI 兼容接口）、FastAPI 后端与 Next.js 前端。

## 项目对比

| 维度 | `langgraph` | `openai-agents` |
| --- | --- | --- |
| 编排方式 | `StateGraph` + 条件边 | `Runner` 串行运行多个 `Agent` |
| 流程 1 | `recognize_emotion` 节点 | `emotion_recognition` Agent |
| 流程 2 | `encourage` / `relieve` / `accompany` 三个节点 | 三个独立策略 Agent，由 Python 选择 |
| 分支控制 | 图的 conditional edge | 解析情感标签后选择 Agent |
| DeepSeek 接入 | `ChatOpenAI` | `OpenAIChatCompletionsModel` + `AsyncOpenAI` |
| 共同点 | FastAPI 统一接口、配置外置、结构化 JSON、单元测试、Next.js 代理 | 同左 |

## 统一架构

```text
浏览器
  │  用户输入
  ▼
Next.js 前端 (React)
  │  POST /api/chat
  ▼
Next.js Route Handler（服务端代理，隐藏 FastAPI 地址）
  │  POST /api/chat
  ▼
FastAPI 后端
  │
  ├─ 进程 1：情感识别（positive / negative / neutral）
  │
  └─ 进程 2：按结果选择鼓励 / 情绪疏导 / 温和陪伴
  │
  ▼
DeepSeek Chat（OpenAI 兼容 Chat Completions）
```

## 目录结构

```text
week15/01/
├── langgraph/
│   ├── backend/        # FastAPI + LangGraph
│   └── frontend/       # Next.js 16 + React 19 + TypeScript
├── openai-agents/
│   ├── backend/        # FastAPI + OpenAI Agents SDK
│   └── frontend/       # Next.js 16 + React 19 + TypeScript
└── README.md
```

两个项目分别包含独立的 `.env.example` / `.env.local.example`，可以单独配置、单独启动。

## 快速开始

环境要求：Python 3.10+、Node.js 20.9+、npm。任选一个项目进入其 `README.md` 查看完整步骤，核心命令：

```bash
# 后端
cd langgraph/backend        # 或 openai-agents/backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # 填入 DEEPSEEK_API_KEY
uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd langgraph/frontend       # 或 openai-agents/frontend
npm install
cp .env.local.example .env.local
npm run dev
```

打开 <http://localhost:3000>。两个前端默认都使用 3000 端口，如需同时运行，请通过 `npm run dev -- --port 3001` 修改其中一个端口，并确保其 `API_BASE_URL` 指向对应后端。

## 设计要点

- **配置外置**：密钥与模型参数只存在于后端 `.env`，前端只持有后端地址，不接触 API Key。
- **统一响应**：两套后端都返回 `reply`、`emotion`、`strategy`、`strategy_label`，便于对比框架本身而非接口差异。
- **三分类而非二分类**：在积极/消极之外增加中性分支，避免把模糊表达强行归为积极或消极。
- **失败可诊断**：缺失密钥返回 503；模型输出无法解析或服务异常返回 502；前端显示可读错误。
- **非医疗建议**：回复提示词明确禁止心理诊断，涉及紧急风险时建议联系可信任的人或当地紧急援助。
