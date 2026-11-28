#!/usr/bin/env python3
"""Deterministic cleanup and descriptive statistics for manually researched ESG items.

Input: UTF-8 JSON object with `items` array. This helper does not crawl the web;
the Skill performs search and source verification, then supplies structured items.
"""
from __future__ import annotations
import argparse, json, re, sys
from collections import Counter
from pathlib import Path

TERMS = {
    "双碳": ["双碳", "碳达峰", "碳中和", "碳减排", "碳市场", "碳交易"],
    "碳管理": ["碳核算", "碳盘查", "碳核查", "碳排放", "碳足迹", "碳认证", "温室气体"],
    "可持续发展": ["可持续发展", "可持续", "esg", "环境社会治理"],
    "绿色供应链": ["绿色供应链", "绿色采购", "供应链"],
    "环境治理": ["污染治理", "节能改造", "生态修复", "环境治理"],
}

def norm(s):
    return re.sub(r"\s+", " ", str(s or "")).strip()

def clean(items):
    seen, out = set(), []
    for raw in items:
        x = dict(raw)
        for k in ("title", "url", "publisher", "buyer", "region", "type", "date", "supplier", "topic"):
            if k in x: x[k] = norm(x[k])
        key = (x.get("url", "").rstrip("/").lower(), re.sub(r"\W", "", x.get("title", "")).lower())
        if key in seen or not x.get("title"): continue
        seen.add(key)
        text = " ".join(str(x.get(k, "")) for k in ("title", "topic", "summary"))
        x["matched_topics"] = [name for name, words in TERMS.items() if any(w.lower() in text.lower() for w in words)]
        x["amount"] = parse_amount(x.get("amount"))
        out.append(x)
    return out

def parse_amount(value):
    if value in (None, "", "未知", "未披露"): return None
    if isinstance(value, (int, float)): return float(value)
    s = str(value).replace(",", "").strip()
    m = re.search(r"([-+]?(?:\d+\.?\d*|\.\d+))\s*(亿元|万元|元)?", s)
    if not m: return None
    n, unit = float(m.group(1)), m.group(2)
    return n * {"亿元": 1e8, "万元": 1e4, "元": 1}.get(unit, 1)

def render(items):
    by_topic, by_type, by_region = Counter(), Counter(), Counter()
    amounts = []
    for x in items:
        by_topic.update(x["matched_topics"])
        by_type[x.get("type") or "未分类"] += 1
        by_region[x.get("region") or "未注明"] += 1
        if x["amount"] is not None: amounts.append(x["amount"])
    lines = ["# ESG 信息整理结果", "", f"- 纳入记录：{len(items)}", f"- 有可解析金额：{len(amounts)}；金额缺失/不可解析：{len(items)-len(amounts)}", "- 金额字段按输入值解析为人民币元；未披露币种/口径不会自动换算。", "", "## 主题计数", ""]
    lines += [f"- {k}：{v}" for k,v in by_topic.most_common()] or ["- 无匹配主题"]
    lines += ["", "## 公告类别", ""] + [f"- {k}：{v}" for k,v in by_type.most_common()]
    lines += ["", "## 地区", ""] + [f"- {k}：{v}" for k,v in by_region.most_common()]
    if amounts:
        vals=sorted(amounts); median=vals[len(vals)//2] if len(vals)%2 else (vals[len(vals)//2-1]+vals[len(vals)//2])/2
        lines += ["", "## 已披露金额（人民币元）", "", f"- 合计：{sum(vals):,.2f}", f"- 中位数：{median:,.2f}", f"- 最小/最大：{min(vals):,.2f} / {max(vals):,.2f}"]
    lines += ["", "## 明细", "", "| 日期 | 标题 | 类别 | 采购方/发布者 | 地区 | 金额 | 主题 | 来源 |", "|---|---|---|---|---|---:|---|---|"]
    for x in items:
        amt = "未披露" if x["amount"] is None else f"{x['amount']:,.2f} 元"
        who = x.get("buyer") or x.get("publisher") or ""
        lines.append(f"| {x.get('date','')} | {x['title'].replace('|','/')} | {x.get('type','')} | {who} | {x.get('region','')} | {amt} | {'、'.join(x['matched_topics'])} | [原文]({x.get('url','')}) |")
    return "\n".join(lines)+"\n"

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("input",type=Path); p.add_argument("-o","--output",type=Path)
    a=p.parse_args(); data=json.loads(a.input.read_text(encoding="utf-8")); items=clean(data.get("items",[])); result=render(items)
    if a.output: a.output.write_text(result,encoding="utf-8")
    else: sys.stdout.write(result)
if __name__ == "__main__": main()
