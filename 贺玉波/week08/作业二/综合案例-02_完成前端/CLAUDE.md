# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概览

**深度研究助手**（综合案例 02）。市场 / 产品同学输入一个研究主题（竞品分析、行业趋势、技术选型、政策解读），工具自动完成检索、阅读、迭代、综合，产出一份带来源引用的研究报告。

**`README.md` 是唯一的规格来源，所有产品要求以此为准。** 后端（FastAPI + OpenAI Agents SDK）与前端（Next.js 15 + Tailwind v4 + Vitest，`frontend/`）均已实现。

注意：`.ipynb_checkpoints/` 是 Jupyter 自动保存的过期副本（README / CLAUDE.md 的旧版本），忽略之，以根目录文件为准。

输入一个主题，输出四类产物：

1. **结构化研究报告**（摘要、分节正文、关键结论、遗留问题）
2. **来源列表**（每条结论关联 URL / 标题 / 来源，可追溯）
3. **研究过程记录**（检索了哪些关键词、读了哪些页面、迭代了几轮）
4. **置信度说明**（结论的可靠程度、信息截止时间；无来源结论必须标注为"模型推断"）

## 核心架构：研究循环（区别于一次性问答）

README.md 定义的核心流程是一个 agentic loop，不是单次问答：

```
规划（拆解子问题）
  → 多轮检索
  → 阅读抽取
  → 判断是否需要补检（不满足则回到检索）
  → 综合生成报告
```

实现时注意：

- **迭代是产品特性**：要保留"判断是否需要补检"这一环，不能做成一次检索就出报告。
- **可追溯是硬要求**：每条结论要能关联到来源 URL / 标题；来源要随报告一起产出。
- **过程记录要落库/落盘**：检索关键词、已读页面、迭代轮数都要记录下来。

## 搜索工具：Bocha 网页搜索 API

- 官方文档：https://bocha-ai.feishu.cn/wiki/RXEOw02rFiwzGSkd9mUcqoeAnNK
- 端点：`POST https://api.bocha.cn/v1/web-search`
- 鉴权：请求头 `Authorization: Bearer <API key>`（key 在项目根 `.env` 的 `BOCHA_API_KEY`，不提交源码）
- 请求体参数：`query`（必填）、`summary`（是否返回摘要）、`count`（返回条数）

README.md 中的示例：

```bash
curl -X POST "https://api.bocha.cn/v1/web-search" \
  -H "Authorization: Bearer $BOCHA_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"天空为什么是蓝色的？","summary":true,"count":10}'
```

## 后端结构（`backend/`）

```
backend/
├── app.py          # FastAPI：/health、POST /api/research、GET /api/research/{rid}、GET /api/research
├── config.py       # 从项目根 .env 读配置（DeepSeek / Bocha key、MAX_ROUNDS 等）
├── models.py       # Pydantic：四类产物（report/report_html/sources/process/confidence）+ 中间结果 schema
├── research.py     # 研究编排：后台执行、on_progress 中间结果逐步落盘、最终落盘
├── engine.py       # ★ 研究引擎（DeepResearch）：组合 agent/ 各角色的流水线（编排器）
├── storage.py      # backend/data/research/{id}.json 读写（threading.Lock）
├── tools.py        # web_search（Bocha，普通 async 函数，返回解析后 list[dict]）
├── templates/      # Jinja2 提示词模板（提示词与代码分离，每个 agent 一个）
│   ├── keyword_agent.jinja2    # 生成搜索关键词
│   ├── summary_agent.jinja2    # 总结搜索结果 → 一段正文文字
│   ├── judge_agent.jinja2      # 判断是否补检 + 生成新关键词
│   ├── report_agent.jinja2     # 报告元信息 JSON（标题/摘要/关键结论/遗留问题）
│   └── report_html_agent.jinja2# 把结构化报告渲染成自包含 HTML
└── agent/          # ★ 多 agent 包（只含角色 agent，不含编排）
    ├── __init__.py      # 导出 BaseAgent + 4 个角色 agent
    ├── __main__.py      # 包级测试 demo（python3 -m backend.agent）
    ├── base.py          # BaseAgent 基类：以 base model 调用 + parse_json（pydantic + ```json``` 兜底）
    ├── keyword.py       # KeywordAgent.generate_keywords(topic) -> list[str]
    ├── summary.py       # SummaryAgent.summarize(topic, keyword, results) -> str（一段正文）
    ├── judge.py         # JudgeAgent.judge(topic, draft_text, sources, searched) -> JudgeDecision
    └── report.py        # ReportAgent.generate(topic, draft, sources, confidence) -> (ReportContent, html)
```

每个 `.py` 文件末尾都有 `if __name__ == "__main__":` 测试 demo（见「编码约定」）。

**agent 与 engine 的分工**：`agent/` 只含**角色 agent**——都是**无工具**的单次 LLM 调用（继承 `BaseAgent`，提示词从 templates/ 渲染），**不直接调搜索**。`engine.py` 的 `DeepResearch` 是**研究引擎（编排器），不是 agent**：它不参与任何 LLM 调用，只做确定性控制流——规划（KeywordAgent）→ 逐关键词 `web_search`（直接函数调用）→ 总结成一段正文文字并**直接累积进报告草稿 `draft`**（SummaryAgent）→ 基于累积草稿判断补检（JudgeAgent，不足则用新关键词进下一轮，最多 `MAX_ROUNDS` 轮）→ 收集来源 + 确定性计算置信度（来源数 ≥12 high、≥5 medium、否则 low；信息截止时间取来源中最新日期，缺省今天） → 报告（ReportAgent）产出结构化报告（元信息 + 草稿映射正文）+ HTML。流程里逐步累积 `process.steps`（plan/search/summarize/judge 每步记录）、`draft`（每关键词一段正文）与 `sources`（按 URL 去重），每完成一轮通过 `on_progress` 回调把**中间结果写盘**（status 保持 running）。

## 环境变量（`.env`，全部由 `backend/config.py` 读取；`start.sh` 也会 source 一遍）

| 变量 | 默认值 | 用途 |
|---|---|---|
| OPENAI_API_KEY | 空 | DeepSeek API key |
| OPENAI_BASE_URL | https://api.deepseek.com/ | LLM 端点 |
| MODEL_NAME | deepseek-v4-flash | LLM 模型 |
| BOCHA_API_KEY | 空 | Bocha 网页搜索 key |
| BOCHA_SEARCH_COUNT | 10 | 每次搜索返回条数 |
| RESEARCH_MAX_ROUNDS | 3 | 检索/判断轮数上限（即 `MAX_ROUNDS`） |
| LLM_RETRIES | 3 | 空输出自动重试次数 |
| FRONTEND_ORIGIN | http://localhost:3000 | 额外 CORS 放行来源（`app.py`） |
| BACKEND_HOST / BACKEND_PORT | 127.0.0.1 / 8000 | 仅 `start.sh` 使用 |

前端开发注意：`app.py` 已配 CORS，除 `FRONTEND_ORIGIN` 外固定放行 `localhost:3000` / `127.0.0.1:3000` / `localhost:8002` / `127.0.0.1:8002`；前端若用其他端口，通过 `FRONTEND_ORIGIN` 添加。

## 常用命令

```bash
# 安装依赖
pip3 install -r requirements.txt

# 启动后端（在项目根目录，自动加载 .env；端口默认 8000，被占用时改 8001）
bash start.sh
# 或：uvicorn backend.app:app --reload --port 8000

# 发起一次研究（立即返回 research_id，后台执行）
curl -X POST http://127.0.0.1:8000/api/research \
  -H 'Content-Type: application/json' \
  -d '{"topic":"你的研究主题"}'

# 轮询研究结果（status: pending/running/completed/failed）
curl http://127.0.0.1:8000/api/research/<research_id>

# 前端（frontend/，Next.js 15 + Tailwind v4 + Vitest）
cd frontend && npm install   # 安装前端依赖
cd frontend && npm run dev   # 前端 dev server（3000）
cd frontend && npm test      # 前端测试（Vitest）
```

命令按 Git Bash / WSL 写法给出；Windows 上如无 `python3` / `pip3` 别名，用 `python` / `pip` 替代。

无测试框架；冒烟验证用 `python3 -c "import backend.app"` 或对接口发 curl。

每个后端模块文件末尾都有 `if __name__ == "__main__":` 测试 demo（见「编码约定」）。纯本地 demo 直接跑：

```bash
python3 -m backend.config            # 配置自检
python3 -m backend.models            # 模型序列化自检
python3 -m backend.storage           # 落盘读写自检（自动清理测试记录）
python3 -m backend.agent.base        # parse_json 兜底 + 模板渲染
python3 -m backend.agent             # agent 包导出自检（__main__.py）
```

涉及网络 / LLM 的 demo（需要 `.env` 里的 DeepSeek / Bocha key 与网络，人工验证用）：

```bash
python3 -m backend.tools                     # 单次 Bocha 搜索
python3 -m backend.agent.keyword             # 生成关键词（单次 LLM）
python3 -m backend.agent.summary             # 总结搜索结果（单次 LLM）
python3 -m backend.agent.judge               # 判断补检（单次 LLM）
python3 -m backend.agent.report              # 生成结构化报告 + HTML（两次 LLM）
python3 -m backend.engine                    # 研究引擎完整流水线（端到端）
python3 -m backend.research                  # 完整研究 + 落盘（端到端）
python3 -m backend.app                       # 打印路由并启动开发服务器
```

## 关键实现决策（改动前先读）

- **Agent 基于 `09-参考代码/openai-agents.py` 的模式**：`agents` SDK + `Runner.run`（async），模型 `deepseek-v4-flash`，指向 `https://api.deepseek.com/`（key 在 `.env`）。绝不在 async 上下文用 `run_sync`。SDK 全局初始化（`set_default_openai_api("chat_completions")`、`set_tracing_disabled(True)`）在 `agent/base.py` 模块级。
- **空输出自动重试**：DeepSeek 偶发返回 `200 OK` 但 `message.content` 为空（与超时无关——超时会抛异常，SDK 默认 client 超时 600s）。`BaseAgent._run` 对空输出自动重试 `LLM_RETRIES` 次（默认 3，`.env` 可配，每次退避 1s）并打 warning。
- **DeepSeek 不支持 `Agent(output_type=...)`**（会发 `response_format: json_schema`，DeepSeek 报错）。因此提示词强制模型输出唯一 JSON，`agent/base.py` 的 `parse_json` 解析（pydantic 直接解析 + ` ```json ``` ` 代码块正则兜底）。
- **提示词与代码分离**：所有 LLM 提示词在 `backend/templates/`，每个 agent 一个模板；`BaseAgent._render` 用 Jinja2 渲染，`call_json(system_vars, user_input, output_cls)` 发起调用并解析。
- **运行方式**：POST 返回 202 + `research_id`，`app.py` 里 `asyncio.create_task(research.run_research(...))` 后台执行；前端轮询 GET。
- **中间结果保存**：`DeepResearch.run(on_progress=...)` 每完成一轮（含规划）回调一次，`research.py` 的 `on_progress` 把当前 `process`（含 `steps`）、`draft` 与 `sources` 写盘（status 保持 running）——轮询可实时看到过程；任何异常也保留已写入的中间结果。`on_progress` 回调**支持同步或异步**（`engine._progress` 用 `inspect.isawaitable` 判断），demo 里传同步函数、`research.py` 传异步函数都行。
- **研究收敛**：子 agent 均无工具（不会空转）；搜索轮数由 `DeepResearch` 的 `max_rounds` 上限兜底（默认 `RESEARCH_MAX_ROUNDS=3`，`.env` 配置），`JudgeAgent` 判 `sufficient` 也会提前结束。
- **过程记录**：`ResearchProcess` 由 DeepResearch 逐步累积——`plan`（初始关键词）、`search_queries`（全部检索关键词）、`reviewed_urls`（搜索结果的来源 URL，去重）、`iterations`（轮数）、`steps`（`ProcessStep`：plan/search/summarize/judge 每步的 detail）。
- **搜索工具**：Bocha web-search（`tools.web_search`，普通 async 函数，返回解析后的 `list[dict]`：title/url/snippet/site_name/date）；不再有 `fetch_page`（总结基于搜索结果摘要）。
- **报告产物**：`ReportAgent.generate` 两次调用——先输出报告元信息 `ReportOutline`（title/summary/key_conclusions/open_questions），**正文分节由草稿段落直接映射**（heading=keyword、body=text，不走 LLM 重新组织）；再把组装好的 `ReportContent` 渲染成完整自包含 HTML（`final_output` 即 HTML 原文，不包 JSON，避免转义出错）。落盘 `ResearchRecord.report`（结构化）+ `report_html`（HTML）。
- **落盘**：JSON 文件 `backend/data/research/{id}.json`，状态机 pending→running→completed/failed。

## 编码约定（改动/新增代码前先读）

- **所有 pydantic 模型都定义在 `backend/models.py`**：包括 agent 的中间输出结构（`KeywordOutput`、`JudgeDecision`、`DraftBlock`、`ReportOutline` 等）。`agent/` 包、`templates/` 里不定义任何 pydantic 模型；agent 需要输出结构时从 `models.py` 导入（见 `agent/keyword.py` 从 `..models` import `KeywordOutput` 的写法）。
- **特例：SummaryAgent 输出纯文字**：它的产物是报告正文段落（字符串），没有结构化字段，因此**不走 JSON 解析**——直接用 `BaseAgent._run` 拿原始输出（模板要求直接输出正文，不用 ```json 包裹，代码里仅兜底去掉 ``` 代码块）。其余 agent（keyword/judge/report）仍输出结构化 JSON 并走 `parse_json`。
- **每个后端 `.py` 文件末尾都要有自己的 `if __name__ == "__main__":` 测试程序**（main 测试 demo）：
  - 纯本地逻辑（config/models/storage/agent.base/agent 包）的 demo 要能直接 `python3 -m backend.xxx` 跑通并打印自检结果；storage 的 demo 自建测试记录并在 `finally` 里清理，不污染真实数据。
  - 涉及网络 / LLM 的 demo（tools/keyword/summary/judge/report/engine/research/app）同样写好 `__main__`，但不自动运行——需要 `.env` 密钥与网络，供人工端到端验证。
  - 包级 demo 放 `__main__.py`（如 `backend/agent/__main__.py`，`python3 -m backend.agent` 触发）；`__init__.py` 只做导出，不放可执行块。
- **日志用标准库 `logging`，每个模块一个 `logger = logging.getLogger(__name__)`**：
  - base.py 记每次 LLM 调用（开始/完成/输出长度，DEBUG 记 system_vars/user_input/final_output）；keyword/summary/judge/report 记语义事件（生成几个关键词、某关键词总结出多少字正文、判断结果）；engine 记规划/每轮检索/判断/完成；research 记任务开始/完成/失败。
  - 服务端在 `app.py` 的 lifespan 里 `logging.basicConfig(level=INFO, ...)` 打开输出（uvicorn 自带 logger 不重复）；各涉及网络/LLM 的 `__main__` demo 里同样 `basicConfig(INFO)` 以便单跑看到。新增 agent 或日志点时保持同样格式。

## 参考代码

- 同级 `综合案例-01`：同类 README 规格的 **FastAPI 后端 + 训练脚本**（`backend/` + `dataset/`，无前端）。
- 父目录 `09-参考代码/`：`openai-agents.py`（agent 范式）、`jinja2_demo.py`、`fastapi_demo.py` 三个 py demo 与 `template/`。
