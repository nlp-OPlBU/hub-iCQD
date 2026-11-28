#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Import externally obtained search results into the local search cache.

Use this when engines are blocked/captcha'd, or when the team has a licensed search API.
The rest of the pipeline (collect -> rank -> render) stays exactly the same, so imported
and live runs are directly comparable.

Input JSON (list):
[
  {"query": "财政部 可持续披露准则 2026年11月", "num": 8,
   "results": [
      {"title": "...", "url": "https://...", "snippet": "...", "date": "2026-11-17"}
   ]}
]

Usage:
  python3 scripts/import_search.py --file data/search_imports.json
  ESG_OFFLINE=1 python3 scripts/collect.py ...
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True)
    ap.add_argument("--default-num", type=int, default=8)
    args = ap.parse_args()
    with open(args.file, encoding="utf-8") as f:
        data = json.load(f)
    n = 0
    for block in data:
        q = block["query"]
        num = block.get("num", args.default_num)
        results = []
        for r in block.get("results", []):
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
                "date": r.get("date") or web.find_date(r.get("title", "") + " " + r.get("snippet", "")),
                "engine": r.get("engine", "imported"),
            })
        web.import_results(q, results, num)
        n += len(results)
        print(f"imported {len(results):2d} -> {q}")
    print(f"total {n} results from {len(data)} queries")


if __name__ == "__main__":
    main()
