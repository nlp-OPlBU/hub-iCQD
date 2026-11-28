#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
作业二 · 一键复现：用同一批数据，量化「有 script」与「无 script」的差别。

运行：
    python3 一键复现.py

只依赖标准库，不联网。读取 数据/ 下的：
    tenders_raw.json        脚本从检索结果中抽出的原始记录（含抽错、缺字段的）
    tenders.json            人工复核后的入样记录
    tender_analysis.json    报告的洞察部分（用于渲染）
输出：
    out/对比指标.txt         本文档引用的各项指标
    out/ESG招标情报报告.md    重新渲染的报告（可与 作业一/报告 对照）
"""
import json
import os
import re
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "数据")
OUT = os.path.join(HERE, "out")
sys.path.insert(0, os.path.join(HERE, "脚本"))

import analyze  # noqa: E402


def load(name):
    with open(os.path.join(DATA, name), encoding="utf-8") as f:
        return json.load(f)


def norm(t):
    return re.sub(r"[\s\-_—|·,，。:：;；!！?？()（）\[\]【】\"'“”‘’/\\]+", "", t or "").lower()


def ngram_jaccard(a, b, n=2):
    A = {a[i:i + n] for i in range(max(1, len(a) - n + 1))}
    B = {b[i:i + n] for i in range(max(1, len(b) - n + 1))}
    return len(A & B) / max(1, len(A | B))


def raw_metrics(rows):
    return {
        "candidates": len(rows),
        "with_amount": sum(1 for r in rows if r.get("amount_wan")),
        "missing_date": sum(1 for r in rows if not r.get("date")),
        "missing_buyer": sum(1 for r in rows if not r.get("buyer")),
        "missing_supplier": sum(1 for r in rows if not r.get("supplier")),
        "low_conf": sum(1 for r in rows if r.get("confidence") == "low"),
        "amount_lt_1w": sum(1 for r in rows if r.get("amount_wan") and r["amount_wan"] < 1),
    }


def dup_metrics(rows):
    """多源转载：同项目名重复、同项目+金额重复。若不扣除会重复计价。"""
    from collections import Counter
    by_name = Counter(norm(r.get("project"))[:20] for r in rows)
    dup_names = {k: v for k, v in by_name.items() if v > 1}
    return {
        "same_project_name_groups": len(dup_names),
        "records_involved": sum(dup_names.values()),
    }


def main():
    os.makedirs(OUT, exist_ok=True)
    raw = load("tenders_raw.json")
    curated = load("tenders.json")
    analysis = load("tender_analysis.json")

    ok, warns = analyze.validate(curated)
    st = analyze.compute(ok)
    rm = raw_metrics(raw)
    dm = dup_metrics(raw)

    lines = []
    lines.append("== 作业二：脚本处理 vs 人工处理（同一批检索结果）==")
    lines.append("")
    lines.append("【原始候选层】脚本抽取（未复核）")
    for k, v in rm.items():
        lines.append(f"  {k:18s} {v}")
    lines.append("")
    lines.append("【多源转载（若不扣除会重复计价）】")
    lines.append(f"  同项目名重复：{dm['same_project_name_groups']} 组，涉及 {dm['records_involved']} 条记录")
    lines.append("")
    lines.append("【入样层】人工复核后")
    lines.append(f"  样本数            {st['n']}")
    lines.append(f"  合计金额          {st['total_wan']:,.1f} 万元")
    lines.append(f"  中位数            {st['median_wan']:,.1f} 万元")
    lines.append(f"  均值              {st['mean_wan']:,.1f} 万元")
    lines.append(f"  最大 / 最小       {st['max_wan']:,.1f} / {st['min_wan']:,.1f} 万元")
    lines.append("")
    lines.append("【按年份】")
    for y, v in st["by_year"].items():
        lines.append(f"  {y}  项目 {v['n']:2d}  合计 {v['sum']:>9,.1f} 万  中位 {v['median']:>7,.1f} 万")
    lines.append("")
    lines.append("【按赛道】")
    for k, v in st["by_cat"].items():
        lines.append(f"  {v['name']:<22s} {v['n']:2d} 单  {v['sum']:>9,.1f} 万  中位 {v['median']:>7,.1f} 万")
    lines.append("")
    lines.append("【校验】")
    lines.append(f"  剔除（无金额/无日期/重复）：{len(warns)} 条")
    for w in warns:
        lines.append("    - " + w)
    lines.append("")
    lines.append("结论：脚本负责「不漏、不重、不算错」，复核负责「纠偏」。")
    lines.append("      无脚本时，上述 6 项原始层指标无法稳定给出，且每次结果不可复现。")

    text = "\n".join(lines)
    with open(os.path.join(OUT, "对比指标.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text)

    prefix = os.path.join(OUT, "ESG招标情报报告")
    md = analyze.render_md(ok, st, analysis)
    html = analyze.render_html(ok, st, analysis)
    with open(prefix + ".md", "w", encoding="utf-8") as f:
        f.write(md)
    with open(prefix + ".html", "w", encoding="utf-8") as f:
        f.write(html)
    print(f"\n已重新渲染：{prefix}.md / .html")


if __name__ == "__main__":
    main()
