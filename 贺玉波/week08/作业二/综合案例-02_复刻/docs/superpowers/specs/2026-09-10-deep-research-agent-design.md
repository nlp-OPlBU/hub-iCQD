# 深度研究助手（Deep Research Agent）设计规格

- 日期：2026-09-10
- 状态：待审阅
- 来源：`README.md`（综合案例 02 · 深度研究助手）

## 1. 目标与产出要求

输入一个研究主题，自动完成**深度研究**：规划 → 多轮检索 → 阅读抽取 → 补检判断 → 综合生成报告。

产出（对应 README 四条要求）：

1. **结构化研究报告**：摘要、分节正文、关键结论、遗留问题
2. **来源列表**：每条结论关联 URL / 标题 / 来源，可追溯
3. **研究过程记录**：检索了哪些关键词、读了哪些页面、迭代了几轮
4. **置信度说明**：结论可靠程度、信息截止时间、无来源结论标注为「模型推断」

交付物形式：

- `outputs/<主题slug>/report.md` —— 结构化报告 + 置信度章节
- `outputs/<主题slug>/sources.md` —— 来源列表
- `outputs/<主题slug>/process.md` —— 研究过程记录
- `outputs/<主题slug>/report.html` —— 同一 `Report` 模型渲染的自包含 HTML（内联 CSS，含引用标注、置信度徽章、来源附录、过程摘要，浏览器直接打开）

## 2. 已确认决策

| 决策点 | 选择 |
|---|---|
| LLM 提供方 | DeepSeek API（`openai` SDK 指向 `https://api.deepseek.com`，模型 `deepseek-v4-pro`，`reasoning_effort="max"`；均可用环境变量覆盖） |
| 搜索工具 | Bocha API（README 提供）：`POST https://api.bocha.cn/v1/web-search`，`{query, summary: true, count}` |
| 交互形式 | 命令行 CLI |
| 实现方案 | 方案 A：手写状态机循环，每阶段一次结构化 LLM 调用 |
| 报告语言 | 中文 |
| 输出展示 | Markdown 三文件 + 自包含 HTML 展示页 |

## 3. 架构与组件

```
                    ┌──────────────────────────────────────────┐
  主题 ──▶ cli.py ─▶│ pipeline.py（状态机主循环）                 │
                    │  规划 → 逐子问题{检索→阅读→补检判断}* → 综合 │
                    └──────┬───────────────┬───────────────┬────┘
                           ▼               ▼               ▼
                       agents.py        search.py        report.py
                       (4 个提示词)     (Bocha 封装)      (md + html)
                           │
                           ▼
                        llm.py (DeepSeek, JSON 模式, 重试, mock)
```

### 3.1 `llm.py` — LLM 客户端

- `openai` SDK，`base_url=https://api.deepseek.com`，`model=deepseek-v4-pro`（环境变量 `DEEPSEEK_MODEL` 可覆盖）
- 请求携带 `reasoning_effort="max"`（环境变量 `DEEPSEEK_REASONING_EFFORT` 可覆盖）；若 API 拒绝该参数则自动降级为不带该参数重试一次
- 核心方法 `chat_json(system_prompt, user_prompt)`：`response_format={"type": "json_object"}`，`json.loads` 解析，失败重试（最多 N 次）
- API key 读 `DEEPSEEK_API_KEY`（支持 `.env`，`python-dotenv`；key 只存 `.env`，不进代码与文档）
- 错误分类：限流/超时 → 指数退避重试；其他错误 → 上抛，由 pipeline 决定降级或终止
- **Mock 模式**：无 key 或 `--mock` 时返回确定性假响应（每个阶段各一份 fixture），保证全流程可离线跑通

### 3.2 `search.py` — Bocha 搜索封装

- `search(query, count=10)` → `list[SearchResult{title, url, snippet}]`
- 每次调用记录：关键词、返回条数、状态（成功/失败），写入过程记录
- 错误处理：非 200 / 超时 → 记失败并返回空列表，由 pipeline 降级
- Mock 模式返回确定性假结果

### 3.3 `models.py` — Pydantic 数据模型

| 模型 | 关键字段 |
|---|---|
| `SubQuestion` | `id, question, status, notes` |
| `ResearchPlan` | `sub_questions[], search_queries{question_id → queries[]}, suggested_rounds` |
| `SearchResult` | `query, title, url, snippet` |
| `ExtractedNote` | `text, source_url, source_title, sub_question_id` |
| `RoundRecord` | `round_no, queries[], pages_read`（本轮送入 reader 的搜索结果条数）`, new_findings, gap_decision{need_more, reason, extra_queries[]}` |
| `Citation` | `id, title, url, source` |
| `Conclusion` | `text, citation_ids[], confidence(高/中/低)` |
| `Confidence` | `overall, info_cutoff_time, notes, unsourced_claims[{text, label="模型推断"}]` |
| `Report` | `title, summary, sections[{heading, body, citation_ids[]}], conclusions[], open_questions[], confidence` |

注：研究过程记录（`RoundRecord[]`）由 pipeline 独立维护并写入 `process.md`，不经过 synthesizer。

### 3.4 `agents.py` — 四阶段提示词与解析

| 阶段 | 输入 | JSON 输出 |
|---|---|---|
| `planner` 规划 | 主题 | 子问题列表 + 每问检索词 + 轮次建议 |
| `reader` 阅读抽取 | 子问题 + 搜索结果（标题/URL/摘要） | 要点列表，每条带 `source_url` |
| `refiner` 补检判断 | 子问题 + 已抽取要点 | `{need_more: bool, reason, extra_queries[]}` |
| `synthesizer` 综合 | 主题 + 全部带来源要点 | 完整 `Report` 结构（分节、结论+引用、遗留问题、置信度） |

提示词要求（写入 system prompt 的硬约束）：中文输出；每条要点必须带来源；无来源支持的内容必须放入 `unsourced_claims` 并标注「模型推断」；`info_cutoff_time` 必须给出。

### 3.5 `pipeline.py` — 状态机编排

```
planner → for each sub_question:
            search(检索词) → reader(抽取)
            → refiner(need_more?)
               ├─ true  → 用 extra_queries 再检索（该子问题 ≤ max_rounds 补充轮）
               └─ false → 下一子问题
          → synthesizer → Report
```

- 预算控制：单子问题补充轮 ≤ `--max-rounds`（默认 2）；总检索次数 ≤ `--max-total-queries`（默认 30），超限停止补充并标注
- 每个阶段通过回调函数向 CLI 汇报进度（当前阶段、轮次、检索词、已得要点数）
- 每轮生成 `RoundRecord` 汇入过程记录

### 3.6 `report.py` — 输出渲染

- Markdown：`report.md` / `sources.md` / `process.md`（对应 README 三条要求 + 置信度章节内嵌 report.md）
- HTML：`assets/report_template.html`（Jinja2 模板）+ 内联 CSS，渲染同一 `Report` 模型：引用角标 `[1][2]`、来源附录、置信度徽章（高/中/低、「模型推断」标签）、过程摘要；自包含、双击即开

### 3.7 `cli.py` — 命令行入口

```bash
python -m deep_research "研究主题" --max-rounds 2 --max-total-queries 30 \
  --output-dir outputs --model deepseek-chat --mock
```

## 4. 错误处理

| 场景 | 处理 |
|---|---|
| LLM 限流/超时 | 指数退避重试（SDK + 自实现），重试耗尽则终止并保留已生成的过程记录 |
| LLM JSON 解析失败 | 重试 N 次；仍失败则阶段降级（planner 失败 → 主题本身作唯一子问题；reader/refiner 失败 → 该子问题标注低置信度跳过；synthesizer 失败 → 用要点直接拼装基础报告） |
| 搜索失败（单个） | 记录失败到过程记录，继续其他查询 |
| 搜索失败（某子问题全部） | 该部分标注低置信度，不中断整体 |
| 无 API key | 提示使用 `--mock` 模式 |

## 5. 测试策略

- **单元测试（pytest）**
  - `models` 校验与序列化
  - `report` 渲染：固定 `Report` fixture，断言 md/html 内容含引用、置信度、「模型推断」标注
  - `refiner` 决策：mock LLM 返回 `need_more=true/false` 两分支
  - `search` 解析：Bocha 响应 fixture JSON
- **集成测试**：`--mock` 端到端跑一个主题 → 断言四个输出文件齐全、内容结构完整
- **真实验收**：用户提供 `DEEPSEEK_API_KEY` 后实际跑一个主题验证效果

## 6. 依赖

- 运行：`openai`、`pydantic`、`httpx`、`python-dotenv`、`jinja2`
- 开发：`pytest`
- Python 3.10+

## 7. 验收标准

1. `--mock` 模式下全流程跑通，四个输出文件齐全
2. 报告中每条结论带来源引用（URL 可追溯），无来源内容标注「模型推断」
3. `process.md` 含检索关键词、读页数、迭代轮数
4. `report.html` 浏览器打开样式正常、引用与来源对得上
5. 真实 key 下跑一个主题产出可读的中文研究报告
