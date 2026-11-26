#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 1 of the ESG tender-intelligence skill: deterministic harvesting.

Pipeline: fixed query grid -> candidate filtering -> page fetch -> regex field extraction
          -> URL/title/amount de-dup -> tenders_raw.json + review digest.

The script deliberately keeps low-confidence rows instead of dropping them, so the LLM
review step can fix them against the stored evidence (no invented numbers).

Usage:
  python3 scripts/collect.py --years 2024 2025 2026 --fetch-max 70
"""
import argparse
import json
import os
import re
import sys
import time
from datetime import datetime

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web      # noqa: E402
import extract  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def norm_title(t):
    return re.sub(r"[\s\-_—|·,，。:：;；!！?？()（）\[\]【】\"'“”‘’/\\]+", "", t or "").lower()


def jaccard(a, b, n=2):
    """Character n-gram Jaccard — char-level Jaccard conflates different Chinese titles."""
    def grams(s):
        return {s[i:i + n] for i in range(max(1, len(s) - n + 1))} if s else set()
    A, B = grams(a), grams(b)
    return len(A & B) / max(1, len(A | B))


def domain_hit(domain, patterns):
    return any(domain.endswith(p) or p in domain for p in patterns)


def build_queries(cfg, years):
    qs = []
    core = {"report", "consulting", "dualcarbon"}
    for cat in cfg["categories"]:
        for q in cat["queries"]:
            qs.append((cat["id"], q))
            if cat["id"] in core:
                for y in years:
                    qs.append((cat["id"], f"{q} {y}"))
    seen, out = set(), []
    for cid, q in qs:
        if q not in seen:
            seen.add(q)
            out.append((cid, q))
    return out


def collect(cfg, years, num, fetch_max):
    filters = cfg["filters"]
    dom = cfg["domains"]
    cat_names = {c["id"]: c["name"] for c in cfg["categories"]}
    candidates, seen_url, seen_title = [], set(), []
    for cid, q in build_queries(cfg, years):
        try:
            results = web.search(q, num=num)
        except Exception as e:
            sys.stderr.write(f"[collect] search err {q}: {e}\n")
            continue
        for r in results:
            url, title, snip = r["url"], r["title"], r.get("snippet", "")
            if "/link?" in url and "so.com" in url:
                continue  # unresolvable search-engine redirect
            if url in seen_url:
                continue
            text = title + " " + snip
            if not any(k in text for k in filters["must_match"]):
                continue
            if any(k in text for k in filters["exclude"]):
                continue
            d = web.domain_of(url)
            if domain_hit(d, dom["block"]):
                continue
            nt = norm_title(title)
            if any(jaccard(nt, t) > 0.88 for t in seen_title):
                continue
            score = 0
            if domain_hit(d, dom["priority"]):
                score += 4
            for k in ["中标", "成交", "结果公告", "结果公示", "候选人"]:
                if k in title:
                    score += 2
            if re.search(r"金额|万元|亿元", snip):
                score += 2
            if r.get("date"):
                score += 1
            seen_url.add(url)
            seen_title.append(nt)
            candidates.append({
                "query_category": cid,
                "query": q,
                "title": title,
                "url": url,
                "domain": d,
                "date": r.get("date", ""),
                "snippet": snip,
                "score": score,
            })
        time.sleep(0.3)

    candidates.sort(key=lambda x: -x["score"])
    print(f"[collect] candidates={len(candidates)}; fetching up to {fetch_max} pages ...")
    fetched = 0
    records = []
    for c in candidates:
        text, status = "", "snippet_only"
        if fetched < fetch_max:
            rec = web.fetch(c["url"], max_chars=12000)
            fetched += 1
            if rec.get("text"):
                text, status = rec["text"], f"ok:{rec['status']}"
            else:
                status = "fail:" + (rec.get("error") or str(rec.get("status")))
            time.sleep(0.4)
        body_for_extract = (text or c["snippet"]) + "\n" + (c.get("date") or "")
        row = extract.extract(c["title"], body_for_extract, c["url"])
        row["date"] = row["date"] or c.get("date", "")
        row.update({
            "query_category": c["query_category"],
            "query": c["query"],
            "title": c["title"],
            "domain": c["domain"],
            "page_status": status,
            "source_snippet": c["snippet"][:300],
        })
        if row["category"] == "other":
            row["category"] = c["query_category"]
        row["category_name"] = cat_names.get(row["category"], row["category"])
        records.append(row)

    # de-dup records: same url, or same project + amount
    uniq, seen = [], set()
    for r in sorted(records, key=lambda x: (x["confidence"] == "high", x["score"] if "score" in x else 0), reverse=True):
        key = (norm_title(r["project"])[:26], round(r["amount_wan"], 1) if r["amount_wan"] else None)
        if r["url"] in seen or (key[1] and key in seen):
            continue
        seen.add(r["url"])
        if key[1]:
            seen.add(key)
        uniq.append(r)
    return uniq, len(candidates)


def write_digest(records, path):
    lines = ["# 招标/中标候选记录（脚本抽取，待人工/AI 复核）", ""]
    for i, r in enumerate(records, 1):
        amt = f"{r['amount_wan']}万" if r["amount_wan"] else "未识别"
        lines.append(f"## [{i:02d}] {r['title']}")
        lines.append(f"- 日期: {r['date'] or '未识别'} | 金额: {amt} ({r['confidence']}) | 类别: {r['category_name']}")
        lines.append(f"- 采购人: {r['buyer'] or '未识别'} | 供应商: {r['supplier'] or '未识别'}")
        lines.append(f"- 项目名: {r['project']}")
        lines.append(f"- 出处: {r['url']} ({r['page_status']})")
        lines.append(f"- 证据: {r['evidence']}")
        lines.append("")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config/queries.yaml"))
    ap.add_argument("--years", nargs="+", default=["2024", "2025", "2026"])
    ap.add_argument("--num", type=int, default=8)
    ap.add_argument("--fetch-max", type=int, default=70)
    ap.add_argument("--out", default=os.path.join(HERE, "data/tenders_raw.json"))
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    records, n_cand = collect(cfg, args.years, args.num, args.fetch_max)

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=1)
    write_digest(records, args.out.replace(".json", "_digest.md"))

    with_amt = [r for r in records if r["amount_wan"]]
    stats = {
        "candidates": n_cand,
        "records": len(records),
        "with_amount": len(with_amt),
        "high_conf": sum(1 for r in records if r["confidence"] == "high"),
        "by_category": {},
        "collected_at": datetime.now().isoformat(timespec="seconds"),
    }
    for r in records:
        stats["by_category"][r["category"]] = stats["by_category"].get(r["category"], 0) + 1
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
