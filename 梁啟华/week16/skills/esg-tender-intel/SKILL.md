---
name: esg-tender-intel
description: >
  ESG/可持续发展/双碳相关招投标情报SKILL。两种模式：模式A对过去24个月
  中标情况做回溯分析（热点招标、金额分布、供应商格局）；模式B每日监控
  新增招标并推送高相关项目。核心是三层关键词库、服务类型分类体系、
  项目编号去重与统一金额口径。
category: Market Intelligence & Monitoring
trigger_phrases:
  - "ESG中标情况汇总"
  - "ESG招标热点分析"
  - "双碳招标监控"
  - "每天推送ESG招标"
  - "ESG招投标情报"
modes:
  - retrospective_24m: 回溯分析
  - daily_monitor: 每日监控推送
shared_rules:
  - three_tier_keyword_library
  - service_type_taxonomy
  - project_id_deduplication
  - amount_basis_normalization
inputs_required:
  - mode
  - time_window
  - region (default: 全国)
  - buyer_type_filter (optional)
  - service_type_filter (optional)
quality_gates:
  - keyword_scope_disclosed
  - dedup_reproducible
  - amount_basis_unified
  - service_tags_complete
  - coverage_disclaimer_present
  - push_noise_filtered
  - no_fabricated_data
---

# ESG/双碳招投标情报

把分散的招标/中标公告变成可判断的市场情报：**回溯看格局（模式 A），监控抓机会（模式 B）**。
所有统计必须可复现：同一批数据、同一规则、同一结果。

## 何时使用

- 用户要“ESG 中标情况汇总 / 招标热点分析” → 模式 A（默认近 24 个月）；
- 用户要“每天推送 ESG 招标 / 双碳招标监控” → 模式 B；
- 用户提供招标数据文件要求清洗、去重、分类、统计 → 按对应模式执行。

**不适用**：单条招标公告解读、无 ESG/双碳相关性的普通采购、要求预测中标结果（本技能不做预测）。

## 输入确认

| 输入 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `mode` | 是 | 由用户措辞判断 | `retrospective_24m` / `daily_monitor` |
| `time_window` | 是 | A：近 24 个月；B：当日/昨日 | 明确起止日期 |
| `region` | 否 | 全国 | 省/市过滤 |
| `buyer_type_filter` | 否 | 不限 | 政府/国企/上市公司/民营 |
| `service_type_filter` | 否 | 不限 | 见服务分类体系 |

开工前复述：“模式 <A/B>，时间 <起止>，地区 <范围>，过滤 <条件>，数据源 <…>。”

> 无联网检索能力时：**不得虚构公告**。要求用户提供导出文件（CSV/Excel），或明确输出
> “无法获取数据”的说明与所需字段清单。

## 共用规则（R1-R4）

### R1 三层关键词库

加载顺序：`assets/keyword-rules.json`（机器可读，准绳）→ `references/keyword-library.md`（说明）。
相关性成立：命中 L1，或同时命中 L2 + L3；仅命中 L3 不算。
**每份输出开头必须披露关键词范围与阈值。**

### R2 服务类型分类

按 `references/service-taxonomy.md` 的八类体系：每条记录**唯一主标签** + 0-3 个副标签；
无法判断填“其他/待定”并列入人工复核，不得留空。

### R3 项目编号去重

按 `references/dedup-and-amount.md`：项目编号归一化（NFKC、去分隔符、大写）；
无编号用“标题+采购人+发布月份”组合键，并披露组合键条数。

### R4 金额口径统一

统一为**万元**；口径优先级：中标 > 合同 > 预算；无金额记 `unknown`，
统计时披露“已知金额覆盖率”。纯数字无单位按元处理并标注推断。

## 模式 A：24 个月回溯

1. **取数**：汇总用户提供的各期数据，或检索 A 级平台导出；记录数据源与时间范围。
2. **清洗**：运行归一化脚本，得到 `clean.csv` 与 `dedup_report.json`；
   核对输入数、去重数、重复率、金额覆盖率是否合理。
3. **打标**：运行分类脚本，得到 `tagged.csv` 与 `tag_summary.json`；
   检查 `other` 占比，>10% 时回补关键词。
4. **统计**（全部基于去重后数据）：
   - 月度趋势（项目数、金额）；
   - 地区分布 Top 5；
   - 采购人类型分布；
   - 服务类型结构（按主标签、按项目数）；
   - 金额分布（总额、均值、中位数、P25/P75/最大值）；
   - 供应商格局：CR5（前五金额占比）、HHI、头部供应商优势领域。
5. **输出**：套用 `references/coverage-and-noise.md` 的“模式 A 输出模板”。
6. **结论**：3-5 条可执行判断（增长中的服务类型、区域机会、竞争强度、进入门槛）。

## 模式 B：每日监控

1. **取数**：检索/接收当日新增公告；获取上次推送基线（已推送项目编号集合）。
2. **清洗去重**：同 R3，与基线合并去重；重复项目仅在状态变化（如发布中标结果）时作为“更新”推送。
3. **打标与过滤**：同 R1/R2；对每条给出相关性分数与理由。
4. **噪声过滤**：按 `references/coverage-and-noise.md` 的规则剔除：
   仅服务词命中、排除词命中、地域不符、已截止、单一采购人刷屏（每日每采购人 ≤2 条）。
5. **推送**：按相关性降序，最多 10 条；每条含采购人、地区、截止时间、金额及口径、
   服务类型、相关性理由、来源链接。
6. **无新增时**：明确写“本期无达到阈值的新增项目”，并给出检索条数与过滤条数。

## 脚本

在技能目录下执行（路径按实际位置调整）：

```bash
# 1) 归一化 + 去重 + 金额统一
python -I scripts/normalize_dedup.py --input <原始数据.csv|json> --outdir <输出目录>

# 2) 关键词打标 + 服务分类 + 噪声过滤
python -I scripts/tag_tenders.py --input <输出目录>/clean.csv --outdir <输出目录>
```

- 输入字段名不同时用 `--id-col/--title-col/--buyer-col/--date-col/--award-col/--budget-col` 覆盖；
- 阈值用 `--min-score` 调整，报告中必须写明实际阈值；
- 两个脚本仅依赖 Python 标准库；输出为 `clean.csv`、`dedup_report.json`、`tagged.csv`、`tag_summary.json`；
- 脚本对已清洗文件可重复运行且结果一致（幂等）。

## 输出格式

- 模式 A：`references/coverage-and-noise.md` 第五节模板；
- 模式 B：同文件第六节模板；
- 所有报告开头必须有“数据覆盖声明”，结尾必须有“口径与局限”。

## 质量门（交付前逐条确认）

| 质量门 | 要求 | 验证方式 |
| --- | --- | --- |
| `keyword_scope_disclosed` | 披露 L1/L2/L3 词数、版本、阈值 | 脚本输出首行 |
| `dedup_reproducible` | 给出输入数、去重数、重复率与去重规则 | `dedup_report.json` |
| `amount_basis_unified` | 金额统一万元，标注口径与覆盖率 | `dedup_report.json` |
| `service_tags_complete` | 每条有主标签，`other` 已列出 | `tagged.csv` + 摘要 |
| `coverage_disclaimer_present` | 数据源/时间/地区/缺口齐全 | 报告首段 |
| `push_noise_filtered` | 模式 B 推送均 ≥ 阈值且附相关性理由 | 推送清单 |
| `no_fabricated_data` | 无编造项目、金额、供应商；缺口显式声明 | 人工核对来源链接 |

## 数据纪律

- 不编造项目名称、编号、金额、中标方；无法获取就说明缺口。
- 区分“预算金额”与“中标金额”，不得混用口径。
- 样例数据仅用于测试，必须在文件内显著标注“虚构样例”。
- 第三方聚合数据必须回源核对后才进入统计。

## 配套资源

| 路径 | 用途 | 何时加载 |
| --- | --- | --- |
| `references/keyword-library.md` | 三层词库说明与范围披露模板 | R1 |
| `references/service-taxonomy.md` | 八类服务定义、判定优先级 | R2 |
| `references/dedup-and-amount.md` | 去重算法、金额换算与报告字段 | R3-R4、A2 |
| `references/coverage-and-noise.md` | 数据源、覆盖率声明、噪声规则、输出模板 | A5、B4-B6 |
| `assets/keyword-rules.json` | 机器可读规则（脚本读取） | 始终 |
| `assets/sample-tenders-raw.csv` | 虚构样例（含重复/噪声/混合单位） | 演示与测试 |
| `scripts/normalize_dedup.py` | 归一化、去重、金额统一 | A2、B2 |
| `scripts/tag_tenders.py` | 打标、分类、噪声过滤 | A3、B3 |

## 常见错误

1. 未去重就统计，导致金额与供应商份额虚高；
2. 把预算金额当中标金额汇总；
3. 只披露关键词不披露阈值，结果不可复现；
4. `other` 标签过多却不回补词库；
5. 模式 B 推送大量仅命中“咨询服务”的低相关项目；
6. 报告没有覆盖率声明，读者误以为是全量数据。
