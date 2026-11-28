#!/usr/bin/env python3
"""Normalize and summarize ESG tender records from a UTF-8 CSV file."""
import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = ["title", "buyer", "location", "notice_date", "status", "category",
          "amount", "currency", "winner", "url", "relevance", "evidence"]

def clean(s):
    return re.sub(r"\s+", " ", (s or "").strip())

def amount_value(raw):
    raw = clean(raw).replace(",", "")
    if not raw:
        return None
    try:
        return Decimal(raw)
    except InvalidOperation:
        return None

def dedupe_key(row):
    # Preserve separate award lots/winners under one project title.
    return "|".join(clean(row.get(k, "")).casefold() for k in
                    ("buyer", "title", "status", "notice_date", "winner", "amount", "currency"))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("--output", type=Path, default=Path("tender-summary.json"))
    ap.add_argument("--start", help="Inclusive ISO date YYYY-MM-DD")
    ap.add_argument("--end", help="Inclusive ISO date YYYY-MM-DD")
    args = ap.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as f:
        rows = [{k: clean(v) for k, v in row.items()} for row in csv.DictReader(f)]

    in_window = [r for r in rows if (not args.start or r.get("notice_date", "") >= args.start)
                 and (not args.end or r.get("notice_date", "") <= args.end)]
    unique, seen = [], set()
    for row in in_window:
        key = dedupe_key(row)
        if key not in seen:
            unique.append(row); seen.add(key)

    statuses, categories, years, amounts = Counter(), Counter(), Counter(), defaultdict(list)
    year_amounts = defaultdict(list)
    for r in unique:
        status = r.get("status", "Unknown") or "Unknown"
        cat = r.get("category", "Unclassified") or "Unclassified"
        statuses[status] += 1; categories[cat] += 1
        if len(r.get("notice_date", "")) >= 4:
            years[r["notice_date"][:4]] += 1
        val = amount_value(r.get("amount", ""))
        if val is not None:
            cur = r.get("currency", "Unspecified") or "Unspecified"
            amounts[(status, cur)].append(val)
            if status.casefold() in {"award", "中标", "成交"} and len(r.get("notice_date", "")) >= 4:
                year_amounts[(r["notice_date"][:4], cur)].append(val)

    amount_summary = {}
    for (status, cur), vals in amounts.items():
        vals = sorted(vals)
        mid = len(vals) // 2
        median = vals[mid] if len(vals) % 2 else (vals[mid-1] + vals[mid]) / 2
        amount_summary.setdefault(status, {})[cur] = {"count": len(vals), "sum": str(sum(vals)),
                               "median": str(median), "min": str(vals[0]), "max": str(vals[-1])}
    yearly_amount_summary = {}
    for (year, cur), vals in year_amounts.items():
        vals.sort(); middle = len(vals) // 2
        median = vals[middle] if len(vals) % 2 else (vals[middle-1] + vals[middle]) / 2
        yearly_amount_summary.setdefault(year, {})[cur] = {
            "count": len(vals), "sum": str(sum(vals)), "median": str(median)
        }
    amount_buckets = Counter()
    for r in unique:
        if (r.get("status", "").casefold() not in {"award", "中标", "成交"}
                or (r.get("currency", "") or "Unspecified") != "万元"):
            continue
        val = amount_value(r.get("amount", ""))
        if val is None: continue
        label = "<50" if val < 50 else "50-<100" if val < 100 else "100-<500" if val < 500 else ">=500"
        amount_buckets[label] += 1
    result = {"input_count": len(rows), "in_window_count": len(in_window), "unique_count": len(unique),
              "duplicate_count": len(in_window)-len(unique), "missing_amount_count": sum(not clean(r.get("amount", "")) for r in unique),
              "status_counts": dict(statuses), "category_counts": dict(categories), "year_counts": dict(years),
              "amounts_by_status_currency": amount_summary, "award_amounts_by_year": yearly_amount_summary,
              "award_amount_buckets_wan": dict(amount_buckets), "records": unique}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "records"}, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
