---
name: "esg-tender-intelligence"
description: "ESG/双碳招标情报：固定检索式抓取中标公告，脚本抽取金额/采购人/供应商并做两年统计，支持每日增量监控与推送。适用于 ESG 招标市场分析、商机挖掘、投标策略、每日中标监测。"
---

# ESG 招标情报 Skill

回答两个问题：**过去两年 ESG/双碳招标市场长什么样？今天又新增了哪些机会？**
原则：检索、抽取、金额归一、统计、去重全部交给脚本；模型只负责复核异常值与撰写洞察。

## 何时使用

- 需要 ESG / 可持续发展 / 双碳 / 碳平台 / 零碳园区 等赛道的中标市场分析；
- 需要每日/每周监控新招标与中标信息，推送到飞书/邮件；
- 需要为客户（默认示例：中国神华）寻找投标机会或供应商对标。

## 目录

```
esg-tender-intelligence/
├── SKILL.md
├── config/queries.yaml            # 正式采集：8 大赛道 + 年份扩展 + 监控检索式
├── config/queries_research.yaml   # 本次研究用检索式（可复制改造）
├── scripts/web.py                 # 多引擎检索 + 正文抓取 + 缓存 + 导入通道
├── scripts/extract.py             # 中标公告字段抽取（金额/日期/采购人/供应商/赛道）
├── scripts/collect.py             # 检索 → 过滤 → 抓取 → 抽取 → 去重 → tenders_raw.json
├── scripts/analyze.py             # 统计 + 图表 + 报告渲染（md/html）
├── scripts/watch.py               # 每日增量监控 + 日报
├── scripts/import_search.py       # 外部检索结果导入（引擎被限流/企业搜索 API）
├── data/                          # 产物：raw / curated / stats / watch
└── data_build/                    # 导入数据的构建脚本（可追溯）
```

## 标准流程（五步）

### Step 1 采集候选

```bash
cd .trae/skills/esg-tender-intelligence
python3 scripts/collect.py --config config/queries.yaml --years 2024 2025 2026 --fetch-max 70
```

- 按赛道生成检索式（含年份扩展），命中「中标/成交/结果公告/候选人」才进入候选；
- 优先抓取原文正文；ccgp 等站点抓不到时保留搜索摘要并标注 `page_status`；
- 输出 `data/tenders_raw.json` 与 `data/tenders_raw_digest.md`（含证据句）。

### Step 2 复核（必须）

脚本会给出 `confidence`（high/medium/low），模型逐条复核并生成 `data/tenders.json`：

1. 金额必须有原文依据，单位统一为**万元**（元÷10000，亿元×10000）；
2. 同一项目多源转载按「项目名 + 金额」去重，保留官方/原始公告链接；
3. 日期取**公告发布日期**，不要把正文中的截止日期当发布日期；
4. 框架协议、单价合同、未披露金额的项目不进入金额统计（可单列商机清单）；
5. 采购人缺失时，可从项目名称/采购平台推断，但需标注为推断值。

`tenders.json` 字段：

```json
{"project": "…", "buyer": "…", "supplier": "…", "amount_wan": 168.0,
 "date": "2026-07-21", "category": "consulting", "buyer_type": "央企/国企",
 "region": "北京", "url": "https://…", "confidence": "high"}
```

赛道 ID：`report / consulting / dualcarbon / platform / carbonasset / zerocarbon / greenfinance / supplychain / other`。

### Step 3 统计与渲染

```bash
python3 scripts/analyze.py --data data/tenders.json \
  --analysis data/tender_analysis.json \
  --out-prefix ../../reports/ESG招标情报报告_2024-2026
```

脚本自动计算：样本量、合计/均值/中位数/分位数、年度趋势、赛道分布、金额区间、
采购方类型、区域、供应商集中度、TOP 15，并生成自包含 HTML（内联 SVG 图表）。

`tender_analysis.json` 只写洞察：`headline / exec_summary / metrics / market_view /
category_insights / shenhua_view / opportunities / monitor / watch_queries / method_note`。

### Step 4 每日监控

```bash
python3 scripts/watch.py --days 2 --num 10 --fetch-max 25 \
  --push-file ../../reports/daily_watch_$(date +%F).md
```

- 状态保存在 `data/seen.json`，只报新链接/新项目；
- 输出可直接粘贴到飞书群的日报（按金额排序）；
- 建议 cron：每日 09:00；每周一生成汇总。

### Step 5 推送（可选）

日报是纯 Markdown，可直接用飞书机器人 webhook 发送，或交给 lark-im skill 发群。
高价值规则建议：金额 ≥100 万即时提醒，其余进入每日摘要。

## 质量红线

- **金额不猜**：没有明确金额就写“未披露”，不进入金额统计。
- **单位归一**：报告只出现“万元/亿元”，并注明折算规则。
- **去重**：镜像站、聚合站、启信宝/证券之星转载只保留一条。
- **可追溯**：每条样本保留 URL；报告附录列出全部样本。
- **样本偏差**：2024 年可核验金额记录少、民企披露少，结论用于趋势判断，不做全市场规模测算。

## 引擎被限流怎么办

脚本内置多引擎（Quark/搜狗/360/Bing）与 30 分钟冷却；全部被限流时：

```bash
# 1) 把已有检索结果（来自企业搜索 API / 人工导出）写入 JSON
python3 scripts/import_search.py --file data/search_imports.json --default-num 10
# 2) 离线跑同一条流水线
ESG_OFFLINE=1 python3 scripts/collect.py --config config/queries_research.yaml --years 2026 --fetch-max 0
```

导入格式见 `scripts/import_search.py`；这样脚本抽取、统计、渲染完全不变，结果可复现。

## 交付清单

- 市场报告：`ESG招标情报报告_2024-2026.html / .md`
- 每日样例：`daily_watch_YYYY-MM-DD.md`
- 结构化数据：`data/tenders.json`（可入库/对接 BI）
- 统计结果：`data/tender_stats.json`
