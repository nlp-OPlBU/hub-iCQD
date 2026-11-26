---
name: "esg-monthly-report"
description: "生成客户专属 ESG 月报：脚本固定检索互联网、去重打分、抓取正文，模型只做有证据编号的解读与建议。适用于月度 ESG 政策/行业/公司/对标动态汇总、ESG 监测报告、客户 ESG 简报。"
---

# ESG 月报生成 Skill

把「过去一个月 ESG 发生了什么、对客户意味着什么」做成一份可直接发给客户/管理层的月报。
核心原则：**检索与统计交给确定性脚本，判断与建议交给模型，每一条判断都必须能点回原文。**

## 何时使用

- 需要给客户（默认示例：中国神华）出 ESG 月报 / 双周报；
- 需要持续监测 ESG 政策、评级、标准、行业实践与对标企业动作；
- 需要把公开信息整理成「事实 → 影响 → 建议」结构的决策材料。

## 目录

```
esg-monthly-report/
├── SKILL.md
├── config/client_shenhua.yaml   # 客户画像：别名、行业、对标、评级、检索式、打分规则
├── scripts/web.py               # 检索（神马/Quark）+ 正文抓取 + 本地缓存
├── scripts/collect.py           # 固定检索式 → 去重/打分/抓正文 → evidence.json / evidence.md
├── scripts/render.py            # evidence.json + analysis.json → 月报 md + 自包含 html
└── data/                        # 运行产物（evidence / analysis / 缓存）
```

## 四步流程

### Step 1 采集证据（脚本，确定性）

```bash
cd .trae/skills/esg-monthly-report
python3 scripts/collect.py --end 2026-11-25 --days 31 --out data/evidence.json
```

- 20 条固定检索式，分 `policy / industry / company / peers` 四组；
- 自动去重（URL + 标题相似度）、按「关键词 + 信源等级 + 时效 + 客户相关度」打分，
  剔除招聘/培训/文库类噪音，并对招标信息降权（招标由另一个 skill 负责）；
- 每条证据保留 `id / 日期 / 域名 / 摘要 / 正文节选 / URL / 是否本月`；
- 输出 `data/evidence.json`、`data/evidence.md`（带 [P01] 编号的可读证据包）。

### Step 2 审阅证据（模型，必须做）

读 `data/evidence.md`，重点看 `本月` 标记的条目。要求：

1. 只把**有明确日期且落在报告期**的条目写进「本月动态」；
2. 无日期或更早的条目只能作为背景，并注明；
3. 摘要与正文冲突时以正文/官方来源为准；
4. 抓取失败的条目（`page_status=fail/only_snippet`）只能引用其标题与摘要，不得扩写细节。

### Step 3 写 analysis.json（模型，唯一需要创作的文件）

```json
{
  "meta": {"issue": "2026年11月刊", "period_label": "2026年11月", "data_cutoff": "2026-11-25"},
  "headline": "一句话判断",
  "exec_summary": [{"point": "要点", "evidence": ["P05", "P06"]}],
  "metrics": [{"label": "本月新增关键动态", "value": "24", "note": "政策 9 / 行业 8 / 公司 7"}],
  "sections": {
    "policy": {"intro": "…", "findings": [{"title": "…", "date": "2026-11-17", "detail": "…",
      "impact": "高", "implication": "对神华意味着…", "evidence": ["P05"]}], "takeaway": "…"},
    "industry": {"findings": [], "practices": [{"company": "…", "action": "…", "result": "…",
      "implication": "…", "evidence": ["I03"]}], "takeaway": "…"},
    "company": {"findings": [], "takeaway": "…"},
    "peers": {"benchmark": [{"company": "…", "action": "…", "implication": "…", "evidence": ["B02"]}], "takeaway": "…"}
  },
  "risks": [{"risk": "…", "level": "高", "signal": "…", "action": "…"}],
  "actions": {"30d": ["…"], "90d": ["…"], "180d": ["…"]},
  "watchlist": [{"when": "2026-12", "event": "…", "why": "…"}],
  "method_note": "方法说明"
}
```

### Step 4 渲染交付物（脚本）

```bash
python3 scripts/render.py --evidence data/evidence.json --analysis data/analysis.json \
  --out-prefix ../../reports/ESG月报_中国神华_2026-11
```

产出 `.md` 与 `.html`（自包含、可打印）。HTML 中的 KPI、证据分布图、本月时间轴、来源表
都由脚本从 `evidence.json` 计算，模型无法篡改数字。

## 质量红线

- **不编造**：金额、评级、日期、政策名称必须来自证据条目；没有就写「未披露」。
- **可追溯**：正文每个判断后带 `[P05]` 形式编号，附录自动生成完整来源表。
- **分清新旧**：本月新增 vs 背景信息必须分开；样本不足时在方法说明中提示。
- **客户视角**：每条政策/行业动态都要回答「对客户的影响」与「建议动作」，不能只做新闻搬运。
- **评级口径**：MSCI / 伦交所 / 秩鼎等评级只写公开报道口径，正式引用前提示复核。

## 检索稳定性（重要）

免费搜索引擎有短时风控，脚本内置了四层防护：

1. **引擎轮询**：每查询按 `quark → sogou` 轮换起点，避免单个引擎被打满；
2. **短冷却 + 退避重试**：命中验证码后冷却 18 秒（`ESG_COOLDOWN_S` 可调），每查询最多重试 3 次；
3. **相关性闸门**：结果必须命中查询主体词，否则丢弃（防止劣质回退结果污染缓存）；
4. **本地缓存**：成功的查询写入 `.cache/`，重跑只补缺口，二次运行通常 1—2 分钟。

实测（2026-11-26）：20 条检索式全量实采 129 条证据、四板块全覆盖、0 条空结果，首轮约 10 分钟、复用缓存后 1.5 分钟。若某轮仍出现空结果，`collect_stats.json` 的 `empty_queries` 会列出缺口，隔几分钟重跑即可补齐。

## 检索被限流 / 企业内网无法直连时

脚本支持“导入检索结果”后离线跑同一条流水线，抽取、打分、渲染逻辑不变：

```bash
python3 scripts/import_search.py --file data/search_imports.json   # 导入
ESG_OFFLINE=1 python3 scripts/collect.py --end 2026-11-25 --days 31 --fetch-top 8
```

导入格式见 `scripts/import_search.py`；也可接入企业自有搜索 API。

## 每月运行

```bash
# 建议每月 1 日 08:00
0 8 1 * * cd /workspace/.trae/skills/esg-monthly-report && \
  python3 scripts/collect.py --days 31 --out data/evidence.json && echo "下一步：审阅 data/evidence.md 并生成 analysis.json"
```

## 换客户

复制 `config/client_shenhua.yaml` 为 `config/client_<name>.yaml`，修改：
`name/short/aliases/peers/core_topics/rating_snapshot` 与四组 `queries`，
然后 `collect.py --client config/client_<name>.yaml`、`render.py --config ...` 即可。

## 故障排查

| 现象 | 处理 |
| --- | --- |
| 某站点 SSL/403，正文抓取失败 | 保留摘要继续，报告中标注「仅摘要」；不要推断细节 |
| 搜索结果重复/镜像多 | 脚本已按 URL + 标题相似度去重；仍重复时补充 negative 关键词 |
| 某月政策条目过少 | 在 queries 中增加具体部委/交易所/评级机构关键词，并扩大 `--days` |
| 需要英文/海外政策 | 在 queries 中加入英文检索式，打分关键词已覆盖 CBAM/ISSB 等 |
