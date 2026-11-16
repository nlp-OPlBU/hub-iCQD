# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目

深度研究助手（Deep Research Agent）：命令行输入研究主题，经「规划子问题 → 多轮 Bocha 检索 → 阅读抽取 → 补检判断 → 综合报告」状态机，输出 `outputs/<主题slug>/` 下五份文件（report.md / sources.md / process.md / report.html / output.json）。规格文档见 `docs/superpowers/specs/2026-09-10-deep-research-agent-design.md`。

## 项目结构

```
├── .env                              # API key（gitignore，不提交）
├── README.md                         # 课程原始需求，唯一产品规格来源
├── requirements.txt                  # 依赖声明（study 环境已齐备，仅作记录）
├── deep_research/                    # 源码包
│   ├── __main__.py                   # python -m deep_research 入口
│   ├── cli.py                        # argparse 接线：mock/真实/缺 key 三分支，组装 Pipeline
│   ├── config.py                     # .env 读取（DeepSeek/Bocha key、模型、预算、输出目录）
│   ├── models.py                     # 全部 Pydantic 模型（四类产物 + 中间结果 schema，别处不得定义模型）
│   ├── llm.py                        # DeepSeek 客户端（JSON 模式+重试）+ MockLLM（关键词分发）+ 三层异常
│   ├── search.py                     # Bocha 封装（防御性解析、date 透传）+ MockSearchClient
│   ├── agents.py                     # AgentRunner：加载 templates/ 提示词 → chat_json → Pydantic 校验
│   ├── pipeline.py                   # ★ 研究引擎（编排器，不做 LLM 调用）：状态机 + 预算 + 降级 + 确定性置信度
│   ├── report.py                     # 五份输出渲染（md×3 + 自包含 HTML + output.json 序列化）
│   ├── mock_data.py                  # MockLLM 离线夹具（DEFAULT_PLAN/READ/REFINE_*/REPORT）
│   ├── assets/report_template.html   # 报告页 Jinja2 模板（学术刊物风，自包含；结构断言锁定）
│   └── templates/                    # 四角色提示词（.jinja2，与代码分离）
│       ├── planner_agent.jinja2      #   规划：主题 → 子问题 + 检索词
│       ├── reader_agent.jinja2       #   阅读抽取：搜索结果 → 带来源要点
│       ├── refiner_agent.jinja2      #   补检判断：要点 → need_more + 补充检索词
│       └── synthesizer_agent.jinja2  #   综合：要点 → Report JSON（置信度由系统计算）
├── tests/                            # pytest，50 个测试（测试先行，注入 stub 隔离外部依赖）
│   ├── test_config.py / test_models.py / test_llm.py / test_search.py
│   ├── test_agents.py / test_pipeline.py / test_report.py / test_templates.py
│   └── test_cli_e2e.py               #   端到端：--mock 全链路断言五份输出
├── outputs/                          # 研究结果（gitignore）
└── docs/superpowers/specs/           # 设计规格文档（架构/错误处理/验收标准）
```

## 命令

所有命令**必须**用 study conda 环境的解释器直连，不得使用 `conda run`，不得碰 base 环境：

```bash
# 运行测试（全量 / 单文件 / 单测试）
A:\conda_envs\study\python.exe -m pytest -v
A:\conda_envs\study\python.exe -m pytest tests/test_llm.py -v
A:\conda_envs\study\python.exe -m pytest tests/test_report.py::test_slugify -v

# 运行（必须从项目根目录执行；--mock 为离线模式，不调真实 API）
A:\conda_envs\study\python.exe -m deep_research "研究主题"
A:\conda_envs\study\python.exe -m deep_research "研究主题" --mock
```

`deep_research/` 是包本身，`python -m deep_research` 在包目录内执行会报 `No module named deep_research`。依赖已在 study 环境齐备（openai 2.54 / pydantic 2.13 / pytest 9.1），requirements.txt 仅作声明。

## 架构

数据流（`pipeline.py` 单点编排）：`cli` → `agents.planner`（拆子问题）→ 逐子问题 {`search`（Bocha）→ `agents.reader`（抽取带来源要点）→ `agents.refiner`（补检判断，可带 `extra_queries` 再检索）} → `agents.synthesizer`（综合 Report）→ `report`（渲染 md + 自包含 HTML）。LLM 调用统一走 `llm.chat_json`（`response_format=json_object` + 重试）。

关键设计（改代码前必须理解）：

- **异常三层 + except 顺序敏感**：`AgentError`（结构/解析失败，`agents.py`）→ 阶段降级；`LLMError`（传输失败，`llm.py`）→ 整体终止但保留过程记录；`LLMParseError(LLMError, AgentError)` 多重继承 —— pipeline 各阶段 `except AgentError` 子句在 `except LLMError` 之前，靠 Python 首个匹配规则让解析失败走阶段降级。**不要调整这些 except 的先后顺序，也不要拆掉多重继承。**
- **MockLLM 关键词分发顺序敏感**：`规划 → 补检 → 抽取 → 综合`。REFINER 提示词含「已抽取要点」，「抽取」分支在前会误分发（有回归测试锁定，`tests/test_llm.py::test_mock_llm_dispatch_with_real_agent_prompts`）。
- **`agents.reader` 在 Pydantic 校验前注入 `sub_question_id`**（`{**item, ...}` 覆盖 LLM 提供的值），保证来源归属可追溯。
- **引用编号自洽链路**：synthesizer 提示词按 `[i]` 编号 → `pipeline.build_citations` 按 `all_notes` 出现顺序 1..n 编号 → HTML 模板 `href="#src-N"` 与 `id="src-N"` 对应。改动任何一环需同步测试 `tests/test_report.py::test_render_html_report_structure`。
- **预算**：单子问题总轮数 ≤ `max_rounds+1`（1 初检 + ≤max_rounds 补检），总检索次数 ≤ `max_total_queries`（检查点在 `pipeline.py` 两处）。
- **置信度由代码确定性计算**（`pipeline._compute_confidence`，不交给 LLM）：总体置信度按来源数分级（≥12 高、≥5 中、其余低）；信息截止时间取 `SearchResult.date` 中最新者（Bocha 提供日期时透传），无日期时取研究执行日；LLM 只产出 `confidence.unsourced_claims`（模型推断标注），pipeline 合成最终 `Confidence` 时保留它们。
- **提示词与代码分离**：四个角色提示词在 `deep_research/templates/*.jinja2`，`agents.py` 模块加载时经 Jinja2 渲染为 `PLANNER_SYSTEM` 等常量。改提示词只动模板文件；模板中的关键词「规划/补检/抽取/综合」是 MockLLM 分发承重键，不可删除（有回归测试锁定）。
- **`output.json` 结构化落盘**：`write_outputs` 同时写五份文件，`output.json` 完整保存 `{topic, report, process, total_queries}`（可反序列化回模型，支持换模板重渲染历史报告）。
- 报告模板 `assets/report_template.html` 是 Jinja2 自包含页面（学术刊物风），结构断言测试锁定锚点/印章/区块标题，视觉改动不得破坏这些结构。

## 约束

- API key（DeepSeek/Bocha）只存在于 `.env`（已被 gitignore），绝不硬编码或写入任何输出文件。
- `outputs/`、`__pycache__/`、`.pytest_cache/` 已 gitignore，提交时不要包含。
- 报告语言中文；无来源结论必须带「模型推断」标注；置信度只允许「高/中/低」。
