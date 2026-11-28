#!/usr/bin/env python3
"""Check required headings and source links in an ESG monthly Markdown report."""
import argparse
import re
from pathlib import Path

REQUIRED = [
    "本月 ESG 重点观察", "重点议题分析", "同行企业 ESG 实践",
    "风险与机会观察", "下月重点跟踪事项", "来源与研究限制",
]

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("report", type=Path)
    ap.add_argument("--month", help="Expected report month, YYYY-MM")
    args = ap.parse_args()
    text = args.report.read_text(encoding="utf-8")
    errors, warnings = [], []
    for heading in REQUIRED:
        if not re.search(r"^#{1,3}\s+" + re.escape(heading) + r"\s*$", text, re.M):
            errors.append("missing required section: " + heading)
    if args.month:
        try:
            year, month = (int(part) for part in args.month.split("-", 1))
            month_forms = {args.month, f"{year}年{month}月", f"{year}年{month:02d}月"}
            if not any(value in text.splitlines()[0] for value in month_forms):
                errors.append("first heading does not include expected month: " + args.month)
        except (ValueError, TypeError):
            ap.error("--month must use YYYY-MM")
    links = re.findall(r"\[[^\]]+\]\((https?://[^)]+)\)", text)
    if not links:
        errors.append("no Markdown source links found")
    if len(set(links)) < 3:
        warnings.append("fewer than three distinct linked sources; review source coverage")
    if not re.search(r"检索日期|检索时间", text):
        warnings.append("retrieval date/time is not stated")
    for message in errors:
        print("ERROR: " + message)
    for message in warnings:
        print("WARNING: " + message)
    print(f"links={len(links)} distinct_links={len(set(links))} errors={len(errors)} warnings={len(warnings)}")
    raise SystemExit(1 if errors else 0)

if __name__ == "__main__":
    main()
