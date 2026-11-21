# OpenAI Agents 情感识别与陪伴系统

基于 **OpenAI Agents SDK (`openai-agents`)** 与 **FastAPI** 实现的情感识别与回应系统，包含情感识别、条件路由（正向鼓励 / 负面排解）、后端 API 服务以及命令行前端交互进程。

## 功能与架构

项目按以下四个模块串联工作流：

1. **进程 1 — 情感识别**（`backend/sentiment.py`）
   - 使用 `Agent` + `Runner.run` 对用户输入进行情感分类，输出结构化结果 `SentimentResult`（`positive` 或 `negative` 及简短原因 `reason`）。
2. **进程 2 — 情感回应**（`backend/response.py`）
   - **正向分支**（`generate_encouragement`）：由 `EncouragementAgent` 对积极情绪给予真诚的肯定与鼓励。
   - **负面分支**（`generate_comfort`）：由 `ComfortAgent` 对消极情绪先共情安抚，再给出温和可行的排解建议。
3. **后端进程 — 工作流编排与 HTTP 服务**（`backend/workflow.py`、`backend/main.py`）
   - `run_sentiment_workflow` 串联进程 1 与进程 2。
   - FastAPI 暴露 `/health` 和 `POST /api/chat` 接口供前端调用。
4. **前端进程 — 命令行交互客户端**（`frontend/app.py`）
   - 使用 Python 标准库实现，循环读取用户输入并请求后端 `/api/chat` 接口展示结果。

## 目录结构

```text
openai_agents/
├── .env.example          # 环境变量示例配置
├── .gitignore            # Git 忽略规则（忽略 .env、__pycache__ 等）
├── requirements.txt      # Python 依赖列表
├── README.md             # 项目说明文档
├── backend/
│   ├── __init__.py       # 包标识文件
│   ├── main.py           # FastAPI 服务入口
│   ├── model_config.py   # OpenAI-compatible 模型与客户端配置
│   ├── workflow.py       # 串联进程 1 与进程 2 的工作流编排
│   ├── sentiment.py      # 进程 1：情感识别 Agent
│   └── response.py       # 进程 2：正向鼓励 / 负面排解 Agent
└── frontend/
    └── app.py            # 前端 CLI 交互程序
```

## 快速开始

### 1. 安装依赖

建议在虚拟环境中安装依赖（Python 3.10+）：

```bash
cd 作业1/openai_agents
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，并填入有效的 `DEEPSEEK_API_KEY`：

```bash
cp .env.example .env
```

`.env` 配置说明：

| 变量名 | 说明 | 默认值 |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek（或 OpenAI 兼容接口）API Key（必填） | - |
| `OPENAI_BASE_URL` | 模型接口 Base URL | `https://api.deepseek.com` |
| `OPENAI_MODEL` | 使用的模型名称 | `deepseek-flash` |
| `BACKEND_HOST` | 后端服务监听地址 | `127.0.0.1` |
| `BACKEND_PORT` | 后端服务监听端口 | `8000` |

### 3. 启动后端进程

在 `作业1/openai_agents` 目录下运行：

```bash
python -m backend.main
```

启动后可访问 `http://127.0.0.1:8000/health` 验证服务状态，返回 `{"status": "ok"}` 即表示正常。

### 4. 启动前端进程

新开一个终端，进入 `作业1/openai_agents` 目录后运行：

```bash
python frontend/app.py
```

如果后端地址非默认值，可通过环境变量 `BACKEND_URL` 指定：

```bash
BACKEND_URL=http://127.0.0.1:8000/api/chat python frontend/app.py
```

输入任意文本即可查看情感识别结果与系统回复，输入 `exit`、`quit` 或 `q` 退出。

## API 接口说明

### 健康检查

- **GET** `/health`
- 响应示例：

```json
{"status": "ok"}
```

### 情感对话

- **POST** `/api/chat`
- 请求体：

```json
{
  "text": "今天终于把项目上线了，好开心！"
}
```

- 响应示例：

```json
{
  "user_input": "今天终于把项目上线了，好开心！",
  "sentiment": "positive",
  "sentiment_reason": "用户表达了项目成功上线的喜悦情绪",
  "reply": "..."
}
```

## Docker 容器化运行（作业 2）

本项目根目录下已提供 `Dockerfile` 与 `.dockerignore`，详细打包与运行步骤见 [作业 2 说明](../../作业2/README.md)：

```bash
docker build -t sentiment-openai-agents:latest .
docker run -d --name sentiment-api -p 8000:8000 --env-file .env sentiment-openai-agents:latest
```
