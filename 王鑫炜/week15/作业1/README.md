# 作业1 · 情绪识别与回应

浏览器提交文本和框架选项。FastAPI 后端先将文本交给识别进程，拿到结构化情绪标签后，再把原文和情绪分支交给回应进程，最后将标签和回复返回网页。两个阶段分别由独立的 Python 工作进程执行。

| 版本 | 调度方式 |
| --- | --- |
| [LangGraph](app/langgraph_flow.py) | 用 `StateGraph` 保存文本、情绪和回应。识别节点完成后，条件边选择鼓励或排解节点。 |
| [OpenAI Agents](app/agents_flow.py) | 后端依次调用识别 Agent 和回应 Agent。识别 Agent 用 `output_type` 返回情绪，回应 Agent 使用相应的提示词。 |

正向内容得到针对具体进展的鼓励，负向内容得到共情和一条可尝试的缓解建议。根据作业的二分类要求，情绪倾向不明显的文字归入正向分支，并用温和提问回应。

## 本地运行

在 `作业1` 目录执行以下命令，需要 Python 3.12。

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中填写自己的 `OPENAI_API_KEY`。也可通过 `OPENAI_MODEL` 指定模型，默认使用 `gpt-4o-mini`。如果配置 `OPENAI_BASE_URL`，对应服务需要支持 Chat Completions 和 JSON Schema 结构化输出。

```bash
python -m uvicorn app.main:app --port 8000
```

打开 [情绪回应助手](http://127.0.0.1:8000)，用同一句话切换两个框架比较结果。可以分别输入「今天的演讲很顺利，我特别开心！」和「面试没通过，我很失落。」来触发两条分支。应用将用户输入发送给所配置的模型服务。

## 文件入口

- [app/main.py](app/main.py) 处理网页和 API 请求，启动两个工作进程。
- [app/shared.py](app/shared.py) 定义数据结构、提示词和进程调用。
- [web/index.html](web/index.html) 提供表单、框架选择和回应展示。

实现参考 [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api) 和 [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/agents/)。

## 验证结果

使用模拟模型端点对两个框架做了端到端验证，覆盖正向与负向分支、两阶段调用顺序、空白和超长输入、模型故障与恢复。Chromium 在 1024 px 和 375 px 下均能提交表单并显示对应结果。实际模型的分类与回复需填写 API Key 后体验。
