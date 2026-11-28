---
name: esg-monthly-brief
description: >
  生成月度ESG监测简报，覆盖政策/评级/标准动态、行业最佳实践、
  客户公司动态与对标分析。核心判断逻辑是“传导判断 + 张力识别”，
  而非信息平行罗列。适用于煤炭、能源、重工业等ESG敏感行业。
category: Reporting & Disclosure
trigger_phrases:
  - "ESG月报"
  - "这个月的ESG动态"
  - "ESG月度简报"
  - "汇总ESG信息，以[某公司]为例"
inputs_required:
  - client_company
  - industry
  - time_window (default: past 30 days)
  - peer_companies (optional, default: 2-3 industry peers)
output_structure:
  - policy_and_standards (3-5 items, each with transmission_judgment)
  - industry_practices (2-3 decomposable cases)
  - client_tension_and_peers (1+ tension, 2-3 peer comparisons)
  - monthly_verdict_and_tracking (1 verdict + 3 verifiable tracking points)
quality_gates:
  - transmission_judgment_present
  - peer_cases_decomposable
  - tension_identified_or_explicitly_cleared
  - sources_cited
  - no_fabricated_data
  - tracking_points_falsifiable
---

# ESG 月度监测简报

为某一客户公司生成一份**可执行**的月度 ESG 简报：不是新闻汇总，而是回答“本月的变化如何传导到客户、
客户内部存在什么张力、同行走到哪一步、下个月看什么”。

## 何时使用

满足任一条件时使用：

- 用户说“ESG 月报 / 这个月的 ESG 动态 / 汇总 ESG 信息，以某公司为例”；
- 用户需要针对某一煤炭、能源、重工业企业做环保/双碳/披露相关的月度监测；
- 用户要求“政策对客户有什么影响”而非单纯政策清单。

**不适用**：单条新闻解读、无明确客户主体的行业综述、要求覆盖全行业全议题的年度报告、
纯数据查询（应改用招投标情报技能 `esg-tender-intel`）。

## 输入确认

| 输入 | 必填 | 默认值 | 处理方式 |
| --- | --- | --- | --- |
| `client_company` | 是 | — | 缺失时先问用户，不得用“某公司”占位交付 |
| `industry` | 是 | — | 未给时由客户主营推断并复述确认 |
| `time_window` | 否 | 近 30 天 | 明确起止日期（含发布日） |
| `peer_companies` | 否 | 同行 2-3 家 | 由用户指定优先；未指定则由检索确定并声明选择理由 |

开工前用一句话复述：“将以 `<客户>` 为主体，监测 `<起止日期>` 的 `<行业>` ESG 动态，对标 `<同行>`。”
有歧义先问，不要自行假设客户主体。

## 执行流程

### S0 界定范围

1. 固定时间窗与地区范围；
2. 按 `references/source-guide.md` 选定 A/B 级信源；
3. 输出“检索范围声明”（模板见 `references/workflow-detail.md` 的 S0）。

> 若当前环境无法联网或检索工具不可用：**立即停止**，明确告知用户“无法获取信源”，
> 不得凭记忆生成带具体日期/数字的简报。可改为输出“模板 + 待核实清单”。

### S1 扫描与筛选

1. 按 `references/source-guide.md` 的关键词组检索，收集 ≥8 条候选；
2. 用 `references/workflow-detail.md` 的“影响强度评分表”逐条打分；
3. 保留 3-5 条进入简报，优先“直接约束力 / 成本影响”高的条目。

### S2 写传导判断（核心）

对每一条保留项，按五段式书写：**变化 → 作用机制 → 对客户影响 → 可观测信号 → 置信度**；
格式与反例见 `references/workflow-detail.md` 的 S2。禁止“利好/利空”式空话。

### S3 拆解行业实践

选 2-3 个可拆解案例，按 **问题 → 做法 → 投入与成果 → 可复制条件** 书写；
优先客户同行业、同区域、同工艺路线的案例。标准见 `references/workflow-detail.md` 的 S3。

### S4 识别客户张力

按 `references/workflow-detail.md` 的 S4 提问清单，找出客户“承诺 vs 能力 / 目标 vs 增长”等真实矛盾：

- 至少 1 个，附证据与影响判断；
- 检索后确无，则显式写“未识别到显著张力 + 已检索范围 + 判断依据”，不得编造。

### S5 同行对标

选 2-3 家同行，用 `references/workflow-detail.md` 的 S5 维度表横向对比；缺数据写“未披露”。
结论必须指出客户差距与紧迫性。

### S6 结论与跟踪点

1. 写 1 段月度结论：本月最重要的变化 + 客户下月应做的动作；
2. 写 3 个可证伪跟踪点：时间窗 + 具体信号 + 验证方式 + 判定规则（模板见 S6）。

### S7 自检与交付

1. 按 `references/quality-checklist.md` 逐条自检，填“通过/不通过/已声明”；
2. 运行机器校验（见下）；
3. 输出简报正文 + 检索范围 + 数据缺口声明 + 自检结果。

## 输出格式

严格套用 `references/output-template.md`，四个二级标题不得改名：
`## 一、政策与标准`、`## 二、行业实践`、`## 三、客户张力与同行对标`、`## 四、月度结论与跟踪点`。
每条结论附来源（名称 + 日期 + 链接），无链接时写“来源：机构《文件》日期”。

## 质量门（交付前逐条确认）

| 质量门 | 要求 |
| --- | --- |
| `transmission_judgment_present` | 每条政策都有完整传导链，非空话 |
| `peer_cases_decomposable` | 每个案例四要素齐全 |
| `tension_identified_or_explicitly_cleared` | 有证据的张力，或显式声明无张力及依据 |
| `sources_cited` | 结论均有来源，可核验来源 ≥5 条 |
| `no_fabricated_data` | 无占位符、无无源数字、推断已标注 |
| `tracking_points_falsifiable` | ≥3 个跟踪点，含时间/信号/验证方式/判定 |

判定细节见 `references/quality-checklist.md`。

## 数据纪律

- **不编造**政策名称、文号、日期、评级、企业数据；不确定就不写。
- 区分“事实（有来源）”与“推断（有依据）”，推断标注置信度。
- 无法获取的数据写入“数据缺口”，说明已尝试的信源与对结论的影响。
- 引用企业官网/报告时注明发布年份与页码（如有）。

## 配套资源

| 路径 | 用途 | 何时加载 |
| --- | --- | --- |
| `references/workflow-detail.md` | 评分表、五段式模板、张力清单、对标维度 | S1-S6 需要判断标准时 |
| `references/output-template.md` | 月报 Markdown 模板 | 输出前 |
| `references/source-guide.md` | 信源分级、关键词组、引用格式 | S0-S1 |
| `references/quality-checklist.md` | 质量门判定与人工终检 | S7 |
| `scripts/check_brief.py` | 结构与要素机器校验 | S7 |
| `assets/example-brief.md` | 虚构示例（仅演示/测试，不可当作真实信息） | 需要参考写法时 |

## 机器校验

```bash
python -I scripts/check_brief.py <简报.md>
```

脚本检查四个章节、传导判断关键词、案例四要素、跟踪点数量与要素、来源链接数、占位符；
退出码 0 表示通过。脚本通过不等于事实无误，真实性仍需人工回溯来源。

## 常见错误

1. 把检索结果按时间或主题平铺，没有“所以客户要做什么”；
2. 对标只夸同行，不写客户差距；
3. 张力写成“机遇与挑战并存”；
4. 跟踪点无法验证（“持续关注”）；
5. 关键数据无来源，或引用二手转述；
6. 时间窗外内容混入本期。
