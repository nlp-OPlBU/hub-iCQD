# 作业一：情感陪伴助手（LangGraph / OpenAI Agents 双实现）

同一套业务逻辑用两种 Agent 框架各实现一次，共用一个 FastAPI 入口和一个静态网页前端。

## 目录结构

```text
作业一/
├── app/
│   ├── __init__.py
│   ├── shared.py          # 配置、数据模型、解析工具、共享提示词
│   ├── agents_flow.py     # OpenAI Agents 后端
│   ├── langgraph_flow.py  # LangGraph 后端
│   └── main.py            # FastAPI 入口 + 静态页面托管
├── web/
│   ├── index.html         # 前端页面
│   ├── app.js             # 前端交互逻辑
│   └── style.css          # 前端样式
├── requirements.txt       # 两个后端都运行时的依赖
├── requirements-agents.txt# 只运行 OpenAI Agents 后端的依赖
├── .env.example
└── README.md
```

## 流程

```text
用户在网页输入
   │
   ▼
后端进程（FastAPI /api/chat）
   │
   ├─ 进程 1：情感识别 → positive / negative / neutral
   │
   └─ 进程 2：按识别结果分流
        ├─ positive → 鼓励
        ├─ negative → 排解（共情 + 疏导 + 小建议）
        └─ neutral  → 引导
   │
   ▼
返回 {sentiment, strategy, reply}
```

> 题目中的「进程 1 / 进程 2」是业务流水线里的两个处理阶段：
> LangGraph 用节点和条件边实现，OpenAI Agents 用 Agent 和 `Runner.run` 串联。
> 两种流程都由 `app/main.py` 统一对外提供服务，用 `backend` 参数选择。

- **LangGraph**：`app/langgraph_flow.py`
  `sentiment` 节点做进程 1；`encourage` / `comfort` / `guide` 是进程 2 的三个分支节点，
  由 `add_conditional_edges` 根据情感标签路由。
- **OpenAI Agents**：`app/agents_flow.py`
  进程 1 是一个 `Agent`，通过 `report_sentiment` 工具返回结构化结果；
  进程 2 是三个不同角色的 `Agent`，由后端选择其中一个交给 `Runner.run`。

> DeepSeek 等 OpenAI 兼容端点不支持 `json_schema` 结构化输出，
> 因此 Agents 流程用「工具调用 + `StopAtTools`」获取结构化结果；
> LangGraph 流程让模型输出 JSON 并做容错解析（失败重试一次）。

## 1. 安装依赖

两个后端都要跑：

```bash
pip install -r requirements.txt
```

只跑 Agents 后端（作业二镜像使用）：

```bash
pip install -r requirements-agents.txt
```

## 2. 配置模型

复制 `.env.example` 为 `.env`，填入任意 OpenAI 兼容端点：

```ini
OPENAI_API_KEY=你的key
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-flash
WORKFLOWS=both
```

也兼容 `DEEPSEEK_API_KEY` / `DEEPSEEK_BASE_URL` / `DEEPSEEK_MODEL` 命名。
不配置 Key 时服务仍可启动，`/api/health` 会显示 `configured: false`，聊天接口返回 503 和提示。

## 3. 启动

在“作业一”目录执行：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

然后打开 <http://127.0.0.1:8000>。

`WORKFLOWS` 控制启用的后端：

| 值 | 效果 |
| --- | --- |
| `both`（默认） | 两个后端都启用，网页可切换 |
| `agents` | 只启用 OpenAI Agents，LangGraph 选项会被禁用 |
| `langgraph` | 只启用 LangGraph |
| `agents,langgraph` | 同上，显式列出 |

如果只安装了 `requirements-agents.txt`，请设置 `WORKFLOWS=agents`；
`main.py` 会懒加载后端模块，未启用的实现不会被导入。

## 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 健康检查，不调用模型 |
| POST | `/api/chat` | 统一入口，请求体用 `backend` 选择实现 |
| POST | `/api/agents/chat` | 显式调用 OpenAI Agents 后端 |
| POST | `/api/langgraph/chat` | 显式调用 LangGraph 后端 |
| GET | `/` | 聊天页面 |
| GET | `/static/*` | 前端静态文件 |

`POST /api/chat` 示例：

```json
{
  "message": "今天面试过了，特别开心！",
  "backend": "agents"
}
```

返回：

```json
{
  "backend": "agents",
  "model": "deepseek-flash",
  "sentiment": {
    "label": "positive",
    "emotion": "开心",
    "confidence": 0.95,
    "reason": "分享面试通过的好消息"
  },
  "strategy": "encourage",
  "reply": "恭喜你！……"
}
```

命令行测试：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d "{\"message\":\"最近压力好大，睡不着\",\"backend\":\"agents\"}"
```

## 与作业二的关系

作业二的 Dockerfile 只打包 OpenAI Agents 后端：

- 依赖使用 `requirements-agents.txt`；
- 只复制 `app/` 中 `main.py`、`shared.py`、`agents_flow.py`、`__init__.py`；
- 复制 `web/` 提供页面；
- 运行时设置 `WORKFLOWS=agents`，因此镜像里不需要安装 LangGraph。

详见 `../作业二/Dockerfile`。

## 常见问题

- **端口被占用**：改 `uvicorn --port` 的端口，或设置 `.env` 的 `PORT` 后用 `python -m app.main` 启动。
- **网页显示无法连接后端**：确认 uvicorn 正在运行，并通过 `http://127.0.0.1:8000` 打开页面（不要直接双击 `index.html`）。
- **模型名报错**：把 `OPENAI_MODEL` 换成当前账号可用的模型（如 `deepseek-chat`）。
- **情感识别解析失败**：模型没有按 JSON 格式返回，程序会自动重试一次；仍失败会返回 502 和原因。
- **只装了 Agents 依赖却启用了 LangGraph**：接口会返回 503 提示依赖未安装，设置 `WORKFLOWS=agents` 即可。
