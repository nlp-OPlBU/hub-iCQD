#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 3 of the ESG monthly report skill: render the deliverable.

Inputs:
  data/evidence.json   (from collect.py, deterministic)
  data/analysis.json   (written by the LLM, narrative only, must cite evidence ids)
  config/client_shenhua.yaml

Outputs:
  <prefix>.md     markdown edition
  <prefix>.html   self-contained, print-ready HTML edition (inline CSS/SVG)

All numbers, dates and links in the output come from evidence.json — the renderer
refuses unknown evidence ids instead of silently printing broken citations.
"""
import argparse
import html
import json
import os
import sys
from collections import Counter
from datetime import date

import yaml


def esc(s):
    return html.escape(str(s or ""), quote=True)


def load_json(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def index_evidence(ev):
    idx = {}
    for sid, items in ev["sections"].items():
        for it in items:
            idx[it["id"]] = it
    return idx


def ev_links(ids, idx):
    out = []
    for i in ids or []:
        it = idx.get(i)
        if it:
            out.append(it)
    return out


def section_stats(ev):
    st = {}
    for sid, items in ev["sections"].items():
        st[sid] = {
            "total": len(items),
            "in_window": sum(1 for i in items if i.get("in_window")),
            "fetched": sum(1 for i in items if i.get("fetch_status", "").startswith("ok")),
        }
    return st


def bars_svg(items, width=520, bar_h=26, gap=12, color="#2f6b4f"):
    """Horizontal bar chart as inline SVG (deterministic, no font/asset dependency)."""
    if not items:
        return ""
    maxv = max(v for _, v in items) or 1
    h = len(items) * (bar_h + gap) + 6
    parts = [f'<svg viewBox="0 0 {width} {h}" width="100%" role="img" aria-label="证据数量分布">']
    for i, (label, v) in enumerate(items):
        y = i * (bar_h + gap) + 4
        w = max(4, int((v / maxv) * (width - 190)))
        parts.append(f'<text x="0" y="{y + 18}" font-size="13" fill="#2b2f2c">{esc(label)}</text>')
        parts.append(f'<rect x="150" y="{y}" width="{w}" height="{bar_h}" rx="3" fill="{color}" opacity="0.88"/>')
        parts.append(f'<text x="{150 + w + 8}" y="{y + 18}" font-size="13" fill="#4a514c">{v}</text>')
    parts.append("</svg>")
    return "".join(parts)


def timeline_html(items, idx):
    rows = []
    for eid in items:
        it = idx.get(eid)
        if not it:
            continue
        d = it.get("date") or "日期未标注"
        rows.append(
            f'<li><span class="tl-date">{esc(d)}</span>'
            f'<span class="tl-dot"></span>'
            f'<span class="tl-body"><a href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>'
            f'<em>{esc(it["domain"])}</em></span></li>'
        )
    return '<ul class="timeline">' + "".join(rows) + "</ul>" if rows else ""


def md_report(ev, an, client, stats):
    idx = index_evidence(ev)
    L = []
    meta = an.get("meta", {})
    L.append(f"# {client['client']['short']} ESG 月报 · {meta.get('issue', '')}")
    L.append("")
    L.append(f"> 报告期：{ev['meta']['period']}　|　数据截止：{meta.get('data_cutoff', '')}　|　"
             f"证据条目：{stats_total(stats)}（本月新增 {stats_window(stats)}）")
    L.append("")
    L.append(f"## 本期判断\n\n**{an.get('headline','')}**\n")
    for t in an.get("theses", []):
        refs = "、".join(f"[{i}]" for i in t.get("basis", []))
        L.append(f"### 判断 · 置信度 {t.get('confidence','')}")
        L.append(f"{t.get('claim','')} {refs}")
        if t.get("falsify"):
            L.append(f"> 证伪条件：{t['falsify']}")
        L.append("")
    if not an.get("theses"):
        for p in an.get("exec_summary", []):
            refs = "、".join(f"[{i}]" for i in p.get("evidence", []))
            L.append(f"- {p.get('point','')} {refs}")
    L.append("")
    L.append("### 支撑要点")
    for p in an.get("exec_summary", []):
        refs = "、".join(f"[{i}]" for i in p.get("evidence", []))
        L.append(f"- {p.get('point','')} {refs}")
    L.append("")
    titles = {"policy": "一、政策、标准与评级动态", "industry": "二、行业动态与最佳实践",
              "company": "三、公司动态与 ESG 影响", "peers": "四、对标企业关键行动"}
    for sid in ["policy", "industry", "company", "peers"]:
        sec = an.get("sections", {}).get(sid, {})
        L.append(f"## {titles[sid]}")
        if sec.get("intro"):
            L.append(sec["intro"] + "\n")
        for f in sec.get("findings", []):
            refs = "".join(f" [{i}]" for i in f.get("evidence", []))
            L.append(f"### {f.get('claim') or f.get('title','')}")
            L.append(f"*{f.get('date','')}　·　影响 {f.get('impact','')}　·　置信度 {f.get('confidence','')}*")
            L.append("")
            L.append(f"依据：{f.get('detail','')}{refs}")
            if f.get("implication"):
                L.append(f"判断：{f['implication']}")
            if f.get("falsify"):
                L.append(f"证伪条件：{f['falsify']}")
            L.append("")
        for p in sec.get("practices", []) + sec.get("benchmark", []):
            refs = "".join(f" [{i}]" for i in p.get("evidence", []))
            L.append(f"- **{p.get('company','')}**：{p.get('action','')}"
                     f"{'；结果：' + p['result'] if p.get('result') else ''}"
                     f"{'；启示：' + p['implication'] if p.get('implication') else ''}{refs}")
        if sec.get("takeaway"):
            L.append(f"\n**小结**：{sec['takeaway']}\n")
    L.append("## 五、风险雷达")
    L.append("| 风险 | 等级 | 触发信号 | 建议动作 |")
    L.append("| --- | --- | --- | --- |")
    for r in an.get("risks", []):
        L.append(f"| {r.get('risk','')} | {r.get('level','')} | {r.get('signal','')} | {r.get('action','')} |")
    L.append("")
    L.append("## 六、行动建议")
    acts = an.get("actions", {})
    for k, label in [("30d", "未来 30 天"), ("90d", "未来 90 天"), ("180d", "未来 180 天")]:
        if acts.get(k):
            L.append(f"**{label}**")
            for a in acts[k]:
                L.append(f"- {a}")
            L.append("")
    if an.get("watchlist"):
        L.append("## 七、下月关注清单")
        L.append("| 时间 | 事件 | 关注原因 |")
        L.append("| --- | --- | --- |")
        for w in an["watchlist"]:
            L.append(f"| {w.get('when','')} | {w.get('event','')} | {w.get('why','')} |")
        L.append("")
    L.append("## 附录：方法与来源")
    L.append(f"- 检索式：{ev['meta']['query_count']} 条固定查询（政策/行业/公司/对标四组），"
             f"共 {stats_total(stats)} 条去重证据，其中本月新增 {stats_window(stats)} 条。")
    L.append(f"- 抓取：优先抓取原文正文，失败时保留搜索摘要并标注；"
             f"正文抓取成功 {stats_fetched(stats)} 条。")
    L.append("- 口径：本月 = 报告期内发布日期；未标注日期的条目仅作背景参考。")
    L.append("- 评级与奖项信息来自公开报道，正式引用前建议以公司公告/评级机构原文复核。")
    L.append("")
    L.append("| 编号 | 日期 | 来源 | 标题 |")
    L.append("| --- | --- | --- | --- |")
    for sid, items in ev["sections"].items():
        for it in items:
            L.append(f"| {it['id']} | {it['date'] or '—'} | {it['domain']} | [{it['title']}]({it['url']}) |")
    return "\n".join(L)


def stats_total(st):
    return sum(v["total"] for v in st.values())


def stats_window(st):
    return sum(v["in_window"] for v in st.values())


def stats_fetched(st):
    return sum(v["fetched"] for v in st.values())


CSS = """
:root{--ink:#1d2521;--sub:#5b6660;--green:#14483a;--moss:#2f6b4f;--sand:#e9e0cf;--paper:#fbf9f4;
--line:#ddd5c6;--warn:#a6482c;--amber:#b07a24;--ok:#3d7a5c}
*{box-sizing:border-box}
body{margin:0;background:#efece4;color:var(--ink);
font-family:"PingFang SC","Hiragino Sans GB","Microsoft YaHei",system-ui,sans-serif;line-height:1.75}
.page{max-width:1020px;margin:32px auto;background:var(--paper);padding:56px 64px 48px;
box-shadow:0 18px 50px rgba(28,40,33,.12)}
.serif{font-family:"Songti SC","Noto Serif CJK SC","Source Han Serif SC",serif}
.kicker{letter-spacing:.22em;font-size:12px;color:var(--moss);text-transform:uppercase}
h1{font-size:38px;line-height:1.3;margin:10px 0 6px;font-weight:700;color:var(--green)}
.sub{color:var(--sub);font-size:14px}
.rule{height:3px;background:linear-gradient(90deg,var(--green),var(--sand));margin:22px 0 28px}
.headline{border-left:4px solid var(--moss);padding:14px 20px;background:#f3efe5;font-size:17px;margin:24px 0}
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:26px 0 8px}
.kpi{border-top:2px solid var(--green);padding:10px 4px}
.kpi b{display:block;font-size:30px;color:var(--green);line-height:1.2}
.kpi span{font-size:12px;color:var(--sub)}
h2{font-size:22px;margin:42px 0 14px;padding-bottom:8px;border-bottom:1px solid var(--line);color:var(--green)}
h3{font-size:16px;margin:22px 0 6px}
p{margin:8px 0}
.small{font-size:13px;color:var(--sub)}
.item{border:1px solid var(--line);background:#fff;border-radius:4px;padding:16px 18px;margin:12px 0}
.item h3{margin:0 0 4px}
.tag{display:inline-block;font-size:11px;padding:1px 8px;border-radius:10px;margin-left:8px;
vertical-align:2px;background:#e7efe9;color:var(--moss)}
.tag.high{background:#f6e3dc;color:var(--warn)}
.tag.mid{background:#f6ecd8;color:var(--amber)}
.tag.low{background:#e7efe9;color:var(--ok)}
.ref{font-size:11px;color:#5f6a64;margin-left:4px}
.thesis{border-top:1px solid var(--line);padding:14px 0 10px}
.t-row{display:grid;grid-template-columns:34px 1fr;gap:8px;align-items:start}
.t-idx{font-family:"DIN Alternate",Menlo,monospace;font-size:12px;color:#8a938d;padding-top:5px}
.t-claim{margin:0;font-size:17px;line-height:1.65;font-weight:600;color:#22302a}
.t-meta{margin:4px 0 2px 42px;font-size:12px;color:#6b756f}
.falsify{margin:4px 0 0 42px;font-size:12.5px;color:#7a6152;background:#f6f1e8;
border-left:2px solid #d8c3a5;padding:5px 10px}
.item .meta{font-size:11.5px;color:#7a847e;margin:2px 0 6px;letter-spacing:.02em}
.item .ev{font-size:13px;color:#4b554f;background:#faf8f2;border-left:2px solid #ded6c6;
padding:7px 11px;margin:6px 0}
.item .falsify{margin-left:0}
a{color:var(--green);text-decoration:none;border-bottom:1px dotted #9bb2a6}
a:hover{border-bottom-style:solid}
table{width:100%;border-collapse:collapse;font-size:13.5px;margin:10px 0}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{background:#f1ece1;font-weight:600;color:#38443e}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.timeline{list-style:none;padding:0;margin:8px 0}
.timeline li{display:grid;grid-template-columns:88px 18px 1fr;align-items:start;padding:6px 0}
.tl-date{font-size:12px;color:var(--sub);padding-top:2px}
.tl-dot{width:8px;height:8px;background:var(--moss);border-radius:50%;margin-top:8px;position:relative}
.tl-dot:after{content:"";position:absolute;left:3px;top:10px;width:1px;height:26px;background:var(--line)}
.tl-body em{font-style:normal;font-size:11px;color:#5f6a64;margin-left:8px}
.note{background:#f2eee5;border:1px solid var(--line);padding:14px 18px;font-size:13px;color:#4b554f}
.rating{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.rating div{background:#fff;border:1px solid var(--line);padding:12px 14px}
.rating b{font-size:20px;color:var(--green);display:block}
.print{font-size:12px;color:#5f6a64;margin-top:36px;text-align:center}
@media (max-width:760px){.page{padding:28px 20px;margin:0}.kpis{grid-template-columns:repeat(2,1fr)}
.grid2{grid-template-columns:1fr}.rating{grid-template-columns:1fr}h1{font-size:30px}}
@media print{body{background:#fff}.page{box-shadow:none;margin:0;max-width:none}}
"""


def html_report(ev, an, client, stats):
    idx = index_evidence(ev)
    meta = an.get("meta", {})
    sec_titles = {"policy": "一、政策、标准与评级动态", "industry": "二、行业动态与最佳实践",
                  "company": "三、公司动态与 ESG 影响", "peers": "四、对标企业关键行动"}
    sec_short = {"policy": "政策标准", "industry": "行业实践", "company": "公司动态", "peers": "对标企业"}

    kpis = "".join(
        f'<div class="kpi"><b>{esc(m.get("value"))}</b><span>{esc(m.get("label"))}</span>'
        f'<div class="small">{esc(m.get("note",""))}</div></div>'
        for m in an.get("metrics", [])
    )
    summary = "".join(
        f'<li>{esc(p.get("point"))}<span class="ref">{" ".join("[" + i + "]" for i in p.get("evidence", []))}</span></li>'
        for p in an.get("exec_summary", [])
    )
    thesis_html = "".join(
        f'<div class="thesis"><div class="t-row"><span class="t-idx">{i:02d}</span>'
        f'<p class="t-claim">{esc(t.get("claim",""))}</p></div>'
        f'<p class="t-meta">置信度 {esc(t.get("confidence",""))}　·　依据 '
        + " ".join(f'<a class="ref" href="{esc(idx[x]["url"])}" target="_blank" rel="noopener">[{x}]</a>'
                   for x in t.get("basis", []) if x in idx)
        + f'</p><p class="falsify">证伪条件：{esc(t.get("falsify",""))}</p></div>'
        for i, t in enumerate(an.get("theses", []), 1)
    )

    prof = client["client"]
    rating_html = "".join(
        f'<div><span class="small">{esc(k)}</span><b>{esc(str(v).split("（")[0])}</b>'
        f'<span class="small">{esc(str(v).split("（", 1)[1].rstrip("）") if "（" in str(v) else "")}</span></div>'
        for k, v in prof.get("rating_snapshot", {}).items()
    )

    chart_items = [(sec_short[sid], stats[sid]["in_window"]) for sid in ["policy", "industry", "company", "peers"]]

    # monthly timeline: in-window items ordered by date, max 12
    in_win = []
    for sid, items in ev["sections"].items():
        for it in items:
            if it.get("in_window") and it.get("date"):
                in_win.append(it)
    in_win.sort(key=lambda x: x["date"], reverse=True)
    timeline = "".join(
        f'<li><span class="tl-date">{esc(it["date"])}</span><span class="tl-dot"></span>'
        f'<span class="tl-body"><a href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a>'
        f'<em>{esc(it["domain"])} · {esc(it["id"])}</em></span></li>'
        for it in in_win[:12]
    )

    def render_section(sid):
        sec = an.get("sections", {}).get(sid, {})
        parts = [f'<h2>{sec_titles[sid]}</h2>']
        if sec.get("intro"):
            parts.append(f'<p>{esc(sec["intro"])}</p>')
        for f in sec.get("findings", []):
            lvl = f.get("impact", "")
            cls = "high" if lvl.startswith("高") else ("mid" if lvl.startswith("中") else "low")
            refs = "".join(
                f' <a class="ref" href="{esc(idx[i]["url"])}" target="_blank" rel="noopener">[{i}]</a>'
                for i in f.get("evidence", []) if i in idx
            )
            meta_bits = "　·　".join(x for x in [f.get("date", ""), f"影响 {lvl}" if lvl else "",
                                                f"置信度 {f.get('confidence','')}" if f.get("confidence") else ""] if x)
            parts.append(
                f'<div class="item"><h3>{esc(f.get("claim") or f.get("title",""))}'
                f'{f"<span class=\'tag {cls}\'>{esc(lvl)}</span>" if lvl else ""}{refs}</h3>'
                f'<p class="meta">{esc(meta_bits)}</p>'
                f'<p class="ev">{esc(f.get("detail",""))}</p>'
                + (f'<p>判断：{esc(f["implication"])}</p>' if f.get("implication") else "")
                + (f'<p class="falsify">证伪条件：{esc(f["falsify"])}</p>' if f.get("falsify") else "")
                + "</div>"
            )
        rows = sec.get("practices", []) + sec.get("benchmark", [])
        if rows:
            parts.append('<table><tr><th style="width:18%">企业</th><th style="width:34%">关键行动</th>'
                         '<th style="width:24%">结果 / 进展</th><th>对神华的启示</th></tr>')
            for p in rows:
                refs = "".join(
                    f' <a class="ref" href="{esc(idx[i]["url"])}" target="_blank" rel="noopener">[{i}]</a>'
                    for i in p.get("evidence", []) if i in idx
                )
                parts.append(
                    f'<tr><td><b>{esc(p.get("company",""))}</b></td><td>{esc(p.get("action",""))}</td>'
                    f'<td>{esc(p.get("result",""))}</td><td>{esc(p.get("implication",""))}{refs}</td></tr>'
                )
            parts.append("</table>")
        if sec.get("takeaway"):
            parts.append(f'<div class="note">{esc(sec["takeaway"])}</div>')
        return "".join(parts)

    risks = "".join(
        f'<tr><td>{esc(r.get("risk",""))}</td>'
        f'<td><span class="tag {"high" if r.get("level","").startswith("高") else "mid" if r.get("level","").startswith("中") else "low"}">{esc(r.get("level",""))}</span></td>'
        f'<td>{esc(r.get("signal",""))}</td><td>{esc(r.get("action",""))}</td></tr>'
        for r in an.get("risks", [])
    )
    acts = an.get("actions", {})
    act_html = "".join(
        f'<div class="item"><h3>{label}</h3><ul>{ "".join(f"<li>{esc(x)}</li>" for x in acts.get(k, [])) }</ul></div>'
        for k, label in [("30d", "未来 30 天"), ("90d", "未来 90 天"), ("180d", "未来 180 天")] if acts.get(k)
    )
    watch = "".join(
        f'<tr><td>{esc(w.get("when",""))}</td><td>{esc(w.get("event",""))}</td><td>{esc(w.get("why",""))}</td></tr>'
        for w in an.get("watchlist", [])
    )
    cited = set()
    for t in an.get("theses", []):
        cited.update(t.get("basis", []))
    for sec in an.get("sections", {}).values():
        for f in sec.get("findings", []):
            cited.update(f.get("evidence", []))
        for p in sec.get("practices", []) + sec.get("benchmark", []):
            cited.update(p.get("evidence", []))
    cited = {c for c in cited if c in idx}
    src_rows = []
    for sid in ["policy", "industry", "company", "peers"]:
        for it in ev["sections"][sid]:
            if it["id"] in cited:
                src_rows.append(
                    f'<tr><td>{esc(it["id"])}</td><td>{esc(it["date"] or "—")}</td><td>{esc(it["domain"])}</td>'
                    f'<td><a href="{esc(it["url"])}" target="_blank" rel="noopener">{esc(it["title"])}</a></td></tr>')
    sources = "".join(src_rows)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(prof['short'])} ESG 月报 · {esc(meta.get('issue',''))}</title>
<style>{CSS}</style></head>
<body><div class="page">
<div class="kicker">ESG MONTHLY INTELLIGENCE</div>
<h1 class="serif">{esc(prof['short'])} ESG 月报</h1>
<div class="sub">{esc(meta.get('issue',''))}　·　报告期 {esc(ev['meta']['period'])}　·　数据截止 {esc(meta.get('data_cutoff',''))}　·　证据条目 {stats_total(stats)}（本月新增 {stats_window(stats)}）</div>
<div class="rule"></div>
<div class="headline serif">{esc(an.get('headline',''))}</div>
{'<h2 style="margin-top:34px">三个可被证伪的判断</h2>' + thesis_html if thesis_html else ''}
<div class="kpis">{kpis}</div>
<h2>支撑要点</h2><ul class="serif" style="font-size:15px;line-height:1.9">{summary}</ul>
<div class="grid2" style="margin-top:18px">
  <div><h2 style="margin-top:10px">评级快照</h2><div class="rating">{rating_html}</div>
  <p class="small">口径：公开报道整理，正式引用前请以评级机构原文复核。</p></div>
  <div><h2 style="margin-top:10px">本月证据分布（条）</h2>{bars_svg(chart_items)}</div>
</div>
{render_section('policy')}
{render_section('industry')}
{render_section('company')}
{render_section('peers')}
<h2>本月时间轴</h2>{timeline or '<p class="small">本月无带明确日期的条目。</p>'}
<h2>五、风险雷达</h2>
<table><tr><th style="width:26%">风险</th><th style="width:10%">等级</th><th style="width:30%">触发信号</th><th>建议动作</th></tr>{risks}</table>
<h2>六、行动建议</h2>{act_html}
{f'<h2>七、下月关注清单</h2><table><tr><th style="width:16%">时间</th><th style="width:40%">事件</th><th>关注原因</th></tr>{watch}</table>' if watch else ''}
<h2>附录：方法与来源</h2>
<div class="note">{esc(an.get('method_note',''))}<br>
检索：{ev['meta']['query_count']} 条固定查询，去重后 {stats_total(stats)} 条证据，其中本月新增 {stats_window(stats)} 条，正文抓取成功 {stats_fetched(stats)} 条。未标注日期的条目仅作背景参考。</div>
<p class="small" style="margin-top:14px">证据清单（编号可在正文中点击回原文）：</p>
<table><tr><th style="width:8%">编号</th><th style="width:12%">日期</th><th style="width:16%">来源</th><th>标题</th></tr>{sources}</table>
<div class="print">本报告由 esg-monthly-report skill 生成 · {esc(ev['meta']['collected_at'])} · 仅供内部决策参考</div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--evidence", default="data/evidence.json")
    ap.add_argument("--analysis", default="data/analysis.json")
    ap.add_argument("--config", default="config/client_shenhua.yaml")
    ap.add_argument("--out-prefix", required=True)
    args = ap.parse_args()

    ev = load_json(args.evidence)
    an = load_json(args.analysis)
    with open(args.config, encoding="utf-8") as f:
        client = yaml.safe_load(f)
    stats = section_stats(ev)

    os.makedirs(os.path.dirname(args.out_prefix) or ".", exist_ok=True)
    md = md_report(ev, an, client, stats)
    htm = html_report(ev, an, client, stats)
    with open(args.out_prefix + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(args.out_prefix + ".html", "w", encoding="utf-8") as f:
        f.write(htm)
    print(f"written {args.out_prefix}.md / .html  ({len(md)} chars md, {len(htm)} chars html)")


if __name__ == "__main__":
    main()
