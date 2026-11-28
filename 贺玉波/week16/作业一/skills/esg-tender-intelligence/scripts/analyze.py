#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 of the ESG tender-intelligence skill: statistics + report rendering.

The statistics are computed only from data/tenders.json (curated rows, each with a source
URL). Narrative comes from data/tender_analysis.json written by the LLM.

Usage:
  python3 scripts/analyze.py --data data/tenders.json --analysis data/tender_analysis.json \
      --out-prefix ../reports/ESG招标情报_2024-2026
"""
import argparse
import html
import json
import os
import statistics
from collections import Counter, defaultdict
from datetime import datetime

CATEGORY_NAMES = {
    "report": "ESG/可持续报告编制", "consulting": "ESG 管理咨询/治理体系",
    "dualcarbon": "双碳规划/碳达峰路径", "platform": "碳管理平台/碳核算/碳足迹",
    "carbonasset": "碳资产/CCER/碳汇", "zerocarbon": "零碳园区/绿色工厂",
    "greenfinance": "绿色金融/ESG 评级", "supplychain": "绿色供应链/绿色采购",
    "other": "其他",
}
BINS = [("<20万", 0, 20), ("20–50万", 20, 50), ("50–100万", 50, 100),
        ("100–300万", 100, 300), ("300–1000万", 300, 1000), (">1000万", 1000, 1e18)]


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def fmt_wan(v):
    if v is None:
        return "—"
    if v >= 10000:
        return f"{v/10000:.2f} 亿"
    return f"{v:,.0f} 万"


def pct(v, d):
    return f"{(v/d*100):.0f}%" if d else "—"


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def validate(rows):
    ok, seen, warns = [], set(), []
    for r in rows:
        key = (r.get("project", "")[:24], round(r.get("amount_wan") or 0, 1))
        if not r.get("amount_wan") or not r.get("date"):
            warns.append(f"skip(no amount/date): {r.get('project','')[:50]}")
            continue
        if key in seen:
            warns.append(f"dup: {r.get('project','')[:50]}")
            continue
        seen.add(key)
        ok.append(r)
    return ok, warns


def compute(rows):
    amounts = [r["amount_wan"] for r in rows]
    total = sum(amounts)
    by_year = defaultdict(list)
    by_month = defaultdict(int)
    for r in rows:
        y = r["date"][:4]
        by_year[y].append(r["amount_wan"])
        by_month[r["date"][:7]] += 1
    by_cat = defaultdict(list)
    for r in rows:
        by_cat[r.get("category", "other")].append(r)
    bins = []
    for label, lo, hi in BINS:
        sel = [r for r in rows if lo <= r["amount_wan"] < hi]
        bins.append({"label": label, "n": len(sel), "sum": sum(r["amount_wan"] for r in sel)})
    suppliers = Counter()
    sup_sum = Counter()
    for r in rows:
        s = (r.get("supplier") or "").strip()
        if s and len(s) <= 40:
            suppliers[s] += 1
            sup_sum[s] += r["amount_wan"]
    buyers = Counter()
    buyer_sum = Counter()
    for r in rows:
        b = (r.get("buyer") or "").strip()
        if b:
            buyers[b] += 1
            buyer_sum[b] += r["amount_wan"]
    stats = {
        "n": len(rows),
        "total_wan": round(total, 1),
        "mean_wan": round(statistics.mean(amounts), 1) if amounts else 0,
        "median_wan": round(statistics.median(amounts), 1) if amounts else 0,
        "p25_wan": round(statistics.quantiles(amounts, n=4)[0], 1) if len(amounts) > 3 else None,
        "p75_wan": round(statistics.quantiles(amounts, n=4)[2], 1) if len(amounts) > 3 else None,
        "max_wan": round(max(amounts), 1) if amounts else 0,
        "min_wan": round(min(amounts), 1) if amounts else 0,
        "by_year": {y: {"n": len(v), "sum": round(sum(v), 1),
                        "median": round(statistics.median(v), 1)} for y, v in sorted(by_year.items())},
        "by_month": dict(sorted(by_month.items())),
        "by_cat": {k: {"n": len(v), "sum": round(sum(x["amount_wan"] for x in v), 1),
                       "median": round(statistics.median([x["amount_wan"] for x in v]), 1),
                       "name": CATEGORY_NAMES.get(k, k)} for k, v in
                   sorted(by_cat.items(), key=lambda kv: -len(kv[1]))},
        "bins": bins,
        "top": sorted(rows, key=lambda r: -r["amount_wan"])[:15],
        "suppliers": suppliers.most_common(12),
        "supplier_amount": sup_sum.most_common(),
        "buyers": buyers.most_common(10),
        "buyer_types": Counter(r.get("buyer_type") or "未识别" for r in rows).most_common(),
        "regions": Counter(r.get("region") or "未标注" for r in rows).most_common(10),
    }
    return stats


def svg_vbars(items, w=560, h=228, color="#2f6b4f", money=False):
    """Vertical bars with labels underneath."""
    if not items:
        return ""
    maxv = max(v for _, v in items) or 1
    pad_l, pad_b, pad_t = 44, 42, 30
    gw = (w - pad_l - 16) / len(items)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for gy in range(4):
        y = pad_t + (h - pad_t - pad_b) * gy / 3
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{w-8}" y2="{y:.1f}" stroke="#e3ddcf"/>')
    for i, (label, v) in enumerate(items):
        bh = (h - pad_t - pad_b) * (v / maxv) if maxv else 0
        x = pad_l + gw * i + gw * 0.18
        bw = gw * 0.64
        y = h - pad_b - bh
        if v > 0:
            parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{max(bh,1):.1f}" rx="3" fill="{color}" opacity=".9"/>')
        txt = f"{v/10000:.2f}亿" if money and v >= 10000 else (f"{v:,.0f}" if not money else f"{v:,.0f}万")
        parts.append(f'<text x="{x+bw/2:.1f}" y="{y-5:.1f}" text-anchor="middle" font-size="11" fill="#4a514c">{txt}</text>')
        parts.append(f'<text x="{x+bw/2:.1f}" y="{h-pad_b+16:.1f}" text-anchor="middle" font-size="12" fill="#2b2f2c">{esc(label)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def svg_hbars(labels, values, w=560, bar_h=24, gap=10, color="#2f6b4f"):
    if not labels:
        return ""
    maxv = max(values) or 1
    h = len(labels) * (bar_h + gap) + 8
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img">']
    for i, (lb, v) in enumerate(zip(labels, values)):
        y = i * (bar_h + gap) + 4
        bw = max(3, int(v / maxv * (w - 250))) if v > 0 else 0
        parts.append(f'<text x="0" y="{y+17}" font-size="12.5" fill="#2b2f2c">{esc(lb)}</text>')
        if bw > 0:
            parts.append(f'<rect x="190" y="{y}" width="{bw}" height="{bar_h}" rx="3" fill="{color}" opacity=".88"/>')
        parts.append(f'<text x="{190+bw+8}" y="{y+17}" font-size="12" fill="#4a514c">{v}</text>')
    parts.append("</svg>")
    return "".join(parts)


CSS = """
:root{--ink:#1c2428;--sub:#5c6a72;--deep:#123a46;--teal:#2b6a72;--copper:#a2603a;--paper:#fbfaf6;
--line:#dcd8cc;--sand:#ece5d6;--warn:#a6482c}
*{box-sizing:border-box}
body{margin:0;background:#eef0ed;color:var(--ink);line-height:1.7;
font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif}
.page{max-width:1060px;margin:32px auto;background:var(--paper);padding:54px 60px 44px;
box-shadow:0 16px 46px rgba(20,40,48,.13)}
.serif{font-family:"Songti SC","Noto Serif CJK SC","Source Han Serif SC",serif}
.kicker{letter-spacing:.22em;font-size:12px;color:var(--teal)}
h1{font-size:36px;margin:10px 0 4px;color:var(--deep);line-height:1.3}
.sub{color:var(--sub);font-size:13.5px}
.rule{height:3px;background:linear-gradient(90deg,var(--deep),var(--sand));margin:20px 0 26px}
.kpis{display:grid;grid-template-columns:repeat(5,1fr);gap:12px;margin:20px 0}
.kpi{border-top:2px solid var(--teal);padding-top:9px}
.kpi b{display:block;font-size:26px;color:var(--deep);line-height:1.25}
.kpi span{font-size:11.5px;color:var(--sub)}
h2{font-size:21px;margin:40px 0 12px;padding-bottom:7px;border-bottom:1px solid var(--line);color:var(--deep)}
h3{font-size:15.5px;margin:20px 0 6px}
p{margin:7px 0}
.small{font-size:12.5px;color:var(--sub)}
.headline{border-left:4px solid var(--copper);background:#f5f0e7;padding:14px 18px;font-size:16.5px;margin:20px 0}
table{width:100%;border-collapse:collapse;font-size:13px;margin:8px 0}
th,td{padding:8px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
th{background:#f0ede3;color:#37454b;font-weight:600}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:22px}
.item{border:1px solid var(--line);background:#fff;padding:14px 16px;margin:10px 0;border-radius:3px}
.item h3{margin:0 0 3px}
.tag{display:inline-block;font-size:11px;padding:1px 7px;border-radius:10px;background:#e4efeb;color:var(--teal);margin-left:6px}
.tag.hi{background:#f7e4dc;color:var(--warn)}
a{color:var(--deep);text-decoration:none;border-bottom:1px dotted #9fb3b6}
a:hover{border-bottom-style:solid}
.note{background:#f2efe6;border:1px solid var(--line);padding:12px 16px;font-size:12.5px;color:#4c565c}
ul.tight{margin:6px 0;padding-left:20px}
ul.tight li{margin:3px 0}
.pill{display:inline-block;background:var(--sand);color:#54483a;border-radius:12px;padding:2px 10px;font-size:12px;margin:2px 4px 2px 0}
.print{font-size:12px;color:#8b9498;margin-top:34px;text-align:center}
@media (max-width:780px){.page{padding:26px 18px;margin:0}.kpis{grid-template-columns:repeat(2,1fr)}
.grid2{grid-template-columns:1fr}h1{font-size:28px}}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none}}
"""


def render_md(rows, st, an):
    L = []
    L.append(f"# ESG 招标情报报告（{an['meta']['period_label']}）")
    L.append("")
    L.append(f"> 样本：{st['n']} 条已披露金额的 ESG/双碳相关中标记录（2025-02 至 2026-11）　|　合计：{fmt_wan(st['total_wan'])}　|　"
             f"中位数：{fmt_wan(st['median_wan'])}　|　数据截止：{an['meta']['data_cutoff']}")
    L.append("")
    L.append(f"## 核心判断\n\n**{an.get('headline','')}**\n")
    for p in an.get("exec_summary", []):
        L.append(f"- {p}")
    L.append("")
    L.append("## 一、市场全景")
    L.append("| 年份 | 项目数 | 披露金额合计 | 中位金额 |")
    L.append("| --- | --- | --- | --- |")
    for y, v in st["by_year"].items():
        L.append(f"| {y} | {v['n']} | {fmt_wan(v['sum'])} | {fmt_wan(v['median'])} |")
    L.append("")
    if an.get("market_view"):
        L.append(an["market_view"] + "\n")
    L.append("### 金额分布")
    L.append("| 区间 | 项目数 | 占比 |")
    L.append("| --- | --- | --- |")
    for b in st["bins"]:
        L.append(f"| {b['label']} | {b['n']} | {pct(b['n'], st['n'])} |")
    L.append("")
    L.append("## 二、热点赛道")
    L.append("| 赛道 | 项目数 | 披露金额 | 中位数 |")
    L.append("| --- | --- | --- | --- |")
    for k, v in st["by_cat"].items():
        L.append(f"| {v['name']} | {v['n']} | {fmt_wan(v['sum'])} | {fmt_wan(v['median'])} |")
    L.append("")
    for c in an.get("category_insights", []):
        L.append(f"- **{c.get('category','')}**：{c.get('insight','')}")
    L.append("")
    L.append("## 三、采购方与供应商")
    if st["buyers"]:
        L.append("| 采购方 | 项目数 |")
        L.append("| --- | --- |")
        for b, n in st["buyers"]:
            L.append(f"| {b} | {n} |")
    if st["suppliers"]:
        L.append("\n| 中标供应商 | 项目数 | 合计金额 |")
        L.append("| --- | --- | --- |")
        amt_map = dict(st["supplier_amount"])
        for s, n in st["suppliers"]:
            L.append(f"| {s} | {n} | {fmt_wan(amt_map[s]) if s in amt_map else '—'} |")
    L.append("")
    L.append("## 四、TOP 15 中标项目")
    L.append("| # | 日期 | 项目 | 采购人 | 中标供应商 | 金额 |")
    L.append("| --- | --- | --- | --- | --- | --- |")
    for i, r in enumerate(st["top"], 1):
        L.append(f"| {i} | {r['date']} | [{r['project']}]({r['url']}) | {r.get('buyer','')} | "
                 f"{r.get('supplier','')} | {fmt_wan(r['amount_wan'])} |")
    L.append("")
    L.append("## 五、中国神华视角")
    for p in an.get("shenhua_view", {}).get("findings", []):
        L.append(f"- {p}")
    L.append("\n**建议动作**")
    for a in an.get("shenhua_view", {}).get("actions", []):
        L.append(f"- {a}")
    L.append("")
    L.append("## 六、投标机会与打法")
    for o in an.get("opportunities", []):
        L.append(f"### {o.get('title','')}\n{o.get('detail','')}\n")
    L.append("## 七、持续监控方案")
    L.append(an.get("monitor", "") + "\n")
    L.append("## 附录：口径与样本")
    L.append(an.get("method_note", ""))
    L.append("")
    L.append("| 日期 | 项目 | 供应商 | 金额 | 来源 |")
    L.append("| --- | --- | --- | --- | --- |")
    for r in sorted(rows, key=lambda x: x["date"]):
        L.append(f"| {r['date']} | {r['project']} | {r.get('supplier','')} | {fmt_wan(r['amount_wan'])} | "
                 f"[{r.get('domain', r['url'].split('/')[2])}]({r['url']}) |")
    return "\n".join(L)


def render_html(rows, st, an):
    year_items = [(y, v["n"]) for y, v in st["by_year"].items()]
    year_money = [(y, v["sum"]) for y, v in st["by_year"].items()]
    cat_labels = [v["name"] for v in st["by_cat"].values()]
    cat_counts = [v["n"] for v in st["by_cat"].values()]
    bin_labels = [b["label"] for b in st["bins"]]
    bin_counts = [b["n"] for b in st["bins"]]

    kpis = "".join(f'<div class="kpi"><b>{esc(m.get("value"))}</b><span>{esc(m.get("label"))}</span></div>'
                   for m in an.get("metrics", []))
    summary = "".join(f"<li>{esc(p)}</li>" for p in an.get("exec_summary", []))

    cat_rows = "".join(
        f'<tr><td>{esc(v["name"])}</td><td>{v["n"]}</td><td>{fmt_wan(v["sum"])}</td>'
        f'<td>{fmt_wan(v["median"])}</td><td>{pct(v["n"], st["n"])}</td></tr>'
        for v in st["by_cat"].values())
    top_rows = "".join(
        f'<tr><td>{i}</td><td>{esc(r["date"])}</td><td><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["project"])}</a></td>'
        f'<td>{esc(r.get("buyer",""))}</td><td>{esc(r.get("supplier",""))}</td>'
        f'<td style="text-align:right">{fmt_wan(r["amount_wan"])}</td></tr>'
        for i, r in enumerate(st["top"], 1))
    sup_amt = dict(st["supplier_amount"])
    suppliers = "".join(f'<tr><td>{esc(s)}</td><td>{n}</td><td>{fmt_wan(sup_amt[s]) if s in sup_amt else "—"}</td></tr>'
                        for s, n in st["suppliers"])
    buyers = "".join(f'<tr><td>{esc(b)}</td><td>{n}</td></tr>' for b, n in st["buyers"][:8])

    insights = "".join(
        f'<div class="item"><h3>{esc(c.get("category",""))}</h3><p>{esc(c.get("insight",""))}</p>'
        + (f'<p class="small">{esc(c.get("evidence",""))}</p>' if c.get("evidence") else "") + "</div>"
        for c in an.get("category_insights", []))
    opps = "".join(
        f'<div class="item"><h3>{esc(o.get("title",""))}</h3><p>{esc(o.get("detail",""))}</p></div>'
        for o in an.get("opportunities", []))
    sh = an.get("shenhua_view", {})
    sh_items = "".join(f"<li>{esc(x)}</li>" for x in sh.get("findings", []))
    sh_acts = "".join(f"<li>{esc(x)}</li>" for x in sh.get("actions", []))
    watch = "".join(f'<span class="pill">{esc(w)}</span>' for w in an.get("watch_queries", []))

    sample_rows = "".join(
        f'<tr><td>{esc(r["date"])}</td><td><a href="{esc(r["url"])}" target="_blank" rel="noopener">{esc(r["project"])}</a></td>'
        f'<td>{esc(r.get("buyer",""))}</td><td>{esc(r.get("supplier",""))}</td>'
        f'<td style="text-align:right">{fmt_wan(r["amount_wan"])}</td></tr>'
        for r in sorted(rows, key=lambda x: x["date"], reverse=True))

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESG 招标情报报告 · {esc(an['meta']['period_label'])}</title><style>{CSS}</style></head>
<body><div class="page">
<div class="kicker">ESG TENDER INTELLIGENCE</div>
<h1 class="serif">ESG 招标情报报告</h1>
<div class="sub">{esc(an['meta']['period_label'])}　·　样本 {st['n']} 条（可核验记录 2025-02 至 2026-11，2024 年末无披露金额样本）　·　数据截止 {esc(an['meta']['data_cutoff'])}</div>
<div class="rule"></div>
<div class="headline serif">{esc(an.get('headline',''))}</div>
<div class="kpis">{kpis}</div>
<h2>核心判断</h2><ul class="tight" style="font-size:14.5px">{summary}</ul>
<h2>一、市场全景</h2>
<div class="grid2">
  <div><p class="small">各年度项目数量</p>{svg_vbars(year_items)}</div>
  <div><p class="small">各年度披露金额（万元）</p>{svg_vbars(year_money, money=True)}</div>
</div>
<p class="small">金额分布（条）</p>{svg_hbars(bin_labels, bin_counts)}
<div class="note">{esc(an.get('market_view',''))}</div>
<h2>二、热点赛道</h2>
<table><tr><th>赛道</th><th>项目数</th><th>披露金额</th><th>中位数</th><th>占比</th></tr>{cat_rows}</table>
{insights}
<h2>三、采购方与供应商</h2>
<div class="grid2">
  <div><h3>高频采购方</h3><table><tr><th>采购方</th><th>单数</th></tr>{buyers}</table></div>
  <div><h3>中标供应商 TOP</h3><table><tr><th>供应商</th><th>单数</th><th>金额</th></tr>{suppliers}</table></div>
</div>
<h2>四、TOP 15 中标项目</h2>
<table><tr><th style="width:5%">#</th><th style="width:11%">日期</th><th>项目</th><th style="width:18%">采购人</th><th style="width:18%">中标供应商</th><th style="width:11%">金额</th></tr>{top_rows}</table>
<h2>五、中国神华视角</h2>
<div class="grid2"><div class="item"><h3>观察</h3><ul class="tight">{sh_items}</ul></div>
<div class="item"><h3>建议动作</h3><ul class="tight">{sh_acts}</ul></div></div>
<h2>六、投标机会与打法</h2>{opps}
<h2>七、持续监控方案</h2>
<div class="item"><p>{esc(an.get('monitor',''))}</p><p class="small">监控检索式：</p>{watch}
<p class="small">建议频率：每日 09:00 增量扫描 + 每周一汇总；推送渠道：飞书群机器人 / 邮件。</p></div>
<h2>附录：口径与样本</h2>
<div class="note">{esc(an.get('method_note',''))}</div>
<table><tr><th style="width:11%">日期</th><th>项目</th><th style="width:18%">采购人</th><th style="width:18%">中标供应商</th><th style="width:11%">金额</th></tr>{sample_rows}</table>
<div class="print">本报告由 esg-tender-intelligence skill 生成 · {esc(an['meta']['data_cutoff'])} · 招标数据以公告原文为准</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/tenders.json")
    ap.add_argument("--analysis", default="data/tender_analysis.json")
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()

    rows_raw = load(args.data)
    rows, warns = validate(rows_raw)
    for w in warns:
        print("[validate]", w)
    st = compute(rows)
    an = load(args.analysis)

    os.makedirs(os.path.dirname(args.out_prefix) or ".", exist_ok=True)
    md = render_md(rows, st, an)
    htm = render_html(rows, st, an)
    with open(args.out_prefix + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(args.out_prefix + ".html", "w", encoding="utf-8") as f:
        f.write(htm)
    with open(os.path.join(os.path.dirname(args.data), "tender_stats.json"), "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, default=str)
    print(f"n={st['n']} total={st['total_wan']}万 median={st['median_wan']}万 -> {args.out_prefix}.md/.html")


if __name__ == "__main__":
    main()
