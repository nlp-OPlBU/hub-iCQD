# week16 · ESG 技能与招标情报

本次作业包含两个可独立运行的 skill，以及一组「有脚本 vs 无脚本」的对照实验。
所有结论都能回溯到原文链接，所有统计都能用仓库内数据复算。

---

## 一、作业一：两个 Skill 与两份报告

### 1.1 交付物

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| ESG 月报技能 | `作业一/skills/esg-monthly-report/` | 固定检索式 + 去重打分 + 正文抓取 + 报告渲染 |
| 招标情报技能 | `作业一/skills/esg-tender-intelligence/` | 中标公告采集 + 字段抽取 + 统计 + 每日监控 |
| 轻量版（只有 SKILL.md） | `作业一/skills/esg-monthly-report-lite/` | 用于对照"没有脚本能做到什么程度" |
| 中国神华 ESG 月报 | `作业一/报告/ESG月报_中国神华_2026-11.html` | 判断优先版式，附可证伪条件 |
| ESG 招标情报报告 | `作业一/报告/ESG招标情报报告_2024-2026.html` | 32 条已披露金额样本 |
| 每日监控样例 | `作业一/报告/daily_watch_2026-11-25.md` | 可直发飞书群的日报格式 |
| 实采证据包 | `作业一/报告/证据包_中国神华_实采_2026-11-25.md` | 129 条实采结果，用于验证覆盖度 |

HTML 是自包含文件（内联 CSS/SVG），双击即可打开，也可直接打印成 PDF。

### 1.2 Skill 怎么用

```bash
# 月报：采集 → 复盘 → 写作 → 渲染
cd 作业一/skills/esg-monthly-report
python3 scripts/collect.py --end 2026-11-25 --days 31 --out data/evidence.json
# 读 data/evidence.md，生成 data/analysis.json（只写判断，不写事实）
python3 scripts/render.py --evidence data/evidence.json --analysis data/analysis.json \
  --config config/client_shenhua.yaml --out-prefix ../../报告/ESG月报_中国神华_2026-11

# 招标：采集 → 复核 → 统计 → 渲染
cd 作业一/skills/esg-tender-intelligence
python3 scripts/collect.py --config config/queries.yaml --years 2024 2025 2026
python3 scripts/analyze.py --data data/tenders.json --analysis data/tender_analysis.json \
  --out-prefix ../../报告/ESG招标情报报告_2024-2026

# 每日增量监控
python3 scripts/watch.py --days 2 --push-file ../报告/daily_watch_$(date +%F).md
```

依赖：Python 3.9+、`requests`、`beautifulsoup4`、`lxml`、`pyyaml`。无数据库、无外部服务。

### 1.3 两个 Skill 的分工

- **月报**回答"这个月发生了什么、对客户意味着什么"。检索式按政策/行业/公司/对标四组固定，
  保证每月覆盖一致；模型只写 `analysis.json`（判断、影响、建议），数字和链接全部来自脚本。
- **招标**回答"市场多大、钱花在哪、今天有什么新机会"。脚本用正则抽取金额/日期/采购人/供应商，
  统一折算为万元，按"项目名 + 金额"去重，避免同一公告多源转载被重复计价。

### 1.4 换客户

复制 `config/client_shenhua.yaml`，改 `name / aliases / peers / rating_snapshot / queries`
即可；`collect.py` 与 `render.py` 都支持 `--client` / `--config` 指定。

---

## 二、作业二：脚本沉淀与效果对照

### 2.1 交付物

| 内容 | 位置 |
| --- | --- |
| 对照实验说明 | `作业二/有script_vs_无script_对比.md` |
| 一键复现脚本 | `作业二/一键复现.py`（仅标准库，不联网） |
| 确定性脚本 | `作业二/脚本/`（web / collect / extract / analyze / watch / import_search） |
| 实验数据 | `作业二/数据/`（76 条原始抽取、32 条入样、检索式） |
| 复现输出 | `作业二/out/`（对比指标 + 重渲染报告） |

### 2.2 怎么复现

```bash
cd 作业二
python3 一键复现.py
```

输出 `out/对比指标.txt` 与重新渲染的报告，应与 `作业一/报告/` 中的数字一致。

### 2.3 关键对照（同一批检索结果）

| 指标 | 无脚本 | 有脚本 |
| --- | --- | --- |
| 处理候选 | 38 条，人工逐条读 | 76 条，正则抽取 |
| 识别含金额记录 | 约 24 条 | 34 条（可复核） |
| 多源重复 | 2 组，易重复计价 | 5 组被识别并去重 |
| 缺失日期 | 9 条 | 4 条，且显式标注 |
| 金额单位 | 元/万元混用 | 统一万元，保留原文 |
| 可复现 | 否 | 是（秒级重跑） |

一句话：**脚本管"不漏、不重、不算错"，模型管"读懂、判断、建议"。**

---

## 三、数据口径与已知限制

1. **金额与日期**逐条回原文核对（中国政府采购网、神华公告、交易所披露、评级报道）。
   月报中"前三季度归母净利 444.03 亿元、同比 3.8%"已与官方三季报 PDF 核对一致；
   "减少直接碳排放约 59.5 万吨""打造全球领先的 ESG 实践典范"已与 2025 年度 ESG 报告原文核对。
2. **招标表约 9 条采购人/地区**是从项目名称或采购平台信息推断的（公告未单列"采购人"栏），
   已在报告方法说明中标注为推断值。
3. **样本偏差**：2024 年末无可核验金额记录，样本实际集中在 2025—2026 年；民营与地方平台披露不足。
   因此"2026 是 2025 的 2.7 倍"用于趋势判断，不代表全市场规模。
4. **检索稳定性**：免费搜索引擎有短时风控（20—60 秒），脚本已做引擎轮询、短冷却、退避重试
   与本地缓存；若某轮出现空结果，`collect_stats.json` 的 `empty_queries` 会列出缺口，重跑即可补齐。
5. **实采与人工核验并存**：`evidence.json` 是核验过的强信源版本，`evidence_live.json` 是
   2026-11-26 的实采版本（129 条）。实采来源更多为聚合站，故报告仍采用核验版。

---

## 四、目录总览

```
week16/
├── README.md
├── .gitignore
├── 作业一/
│   ├── skills/
│   │   ├── esg-monthly-report/          # 月报技能
│   │   ├── esg-monthly-report-lite/     # 只有 SKILL.md 的轻量版
│   │   └── esg-tender-intelligence/     # 招标情报技能
│   ├── 报告/                             # 4 份报告 + 1 份证据包
│   └── 数据/                             # evidence / analysis / tenders / stats
└── 作业二/
    ├── 有script_vs_无script_对比.md
    ├── 一键复现.py
    ├── 脚本/
    ├── 数据/
    └── out/
```
