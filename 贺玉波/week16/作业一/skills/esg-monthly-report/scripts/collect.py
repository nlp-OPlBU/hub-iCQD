#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 1 of the ESG monthly report skill: deterministic evidence collection.

What is deterministic here (i.e. what a script buys you over free-form asking):
  * fixed query set from config  -> same coverage every month
  * fixed scoring / tiering      -> no cherry-picking by mood
  * URL-level de-dup            -> no double counting of mirrors
  * every item keeps url+date+snippet -> every claim in the report is traceable
  * local cache                 -> reruns are cheap and identical

Usage:
  python3 scripts/collect.py --end 2026-11-25 --days 31 \
      --client config/client_shenhua.yaml --out data/evidence.json

Outputs:
  data/evidence.json   full structured evidence (for the LLM + renderer)
  data/evidence.md     human/LLM-readable digest with [P01] style ids
  data/collect_stats.json
"""
import argparse
import json
import os
import random
import re
import sys
import time
from datetime import date, datetime, timedelta

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web  # noqa: E402

PREFIX = {"policy": "P", "industry": "I", "company": "C", "peers": "B"}
NO_FETCH_DOMAINS = ["docin.com", "renrendoc.com", "book118.com", "wenku.", "baike.baidu.com",
                    "zhihu.com", "bilibili.com"]


def norm_text(s):
    s = re.sub(r"[\s\-_—|·,，。:：;；!！?？()（）\[\]【】\"'“”‘’/\\]+", "", s or "")
    return s.lower()


def jaccard(a, b, n=2):
    """Character n-gram Jaccard — char-level Jaccard conflates different Chinese titles."""
    def grams(s):
        return {s[i:i + n] for i in range(max(1, len(s) - n + 1))} if s else set()
    A, B = grams(a), grams(b)
    if not A or not B:
        return 0.0
    return len(A & B) / len(A | B)


def source_tier(domain, tiers):
    for i, key in enumerate(["tier1", "tier2", "tier3"], start=1):
        for d in tiers.get(key, []):
            if domain == d or domain.endswith("." + d):
                return 4 - i  # tier1=3, tier2=2, tier3=1
    return 0


def parse_date(s):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except Exception:
        return None


def score_item(item, section, client, start, end, cfg):
    text = item["title"] + " " + item["snippet"]
    score = 0.0
    hits = [k for k in cfg["positive_keywords"] if k in text]
    score += min(len(hits), 6) * 1.2
    score += source_tier(item["domain"], cfg["source_tiers"]) * 2.2
    d = parse_date(item["date"])
    if d:
        if start <= d <= end:
            score += 8
        elif (end - d).days <= 90:
            score += 3.5
        elif (end - d).days <= 365:
            score += 1.0
        else:
            score -= 1.0
    if any(a in text for a in client["aliases"]):
        score += 3.0 if section == "company" else 1.0
    if section == "peers" and any(p in text for p in client["peers"] if p != client["short"]):
        score += 2.0
    if section == "policy":
        for k in ["财政部", "证监会", "交易所", "准则", "指引", "监管", "评级"]:
            if k in text:
                score += 1.0
    if section == "industry":
        for k in ["煤炭", "煤电", "能源", "行业"]:
            if k in text:
                score += 0.8
    for k in cfg["negative_keywords"]:
        if k in text:
            score -= 4.0
    if "中标" in text or "招标" in text:
        score -= 1.5  # tender noise is the other skill's job
    return round(score, 2), hits


def collect(client, cfg, start, end, num, fetch_top):
    seen_urls, seen_titles = {}, []
    sections_out = {}
    counter = {k: 0 for k in PREFIX}
    gaps = []
    for sec in client["report"]["sections"]:
        sid = sec["id"]
        items = []
        for q in sec["queries"]:
            try:
                results = web.search(q, num=num)
            except Exception as e:
                sys.stderr.write(f"[collect] search failed {q}: {e}\n")
                results = []
            if not results:
                gaps.append(q)
            time.sleep(2.2 + random.uniform(0, 1.6))
            picked = 0
            for r in results:
                url = r["url"]
                if "/link?" in url and "so.com" in url:
                    continue  # unresolvable search-engine redirect
                if url in seen_urls:
                    continue
                nt = norm_text(r["title"])
                if any(jaccard(nt, t) > 0.86 for t in seen_titles):
                    continue
                domain = web.domain_of(url)
                sc, hits = score_item({**r, "domain": domain}, sid, client, start, end, cfg)
                if sc < 0:
                    continue
                seen_urls[url] = True
                seen_titles.append(nt)
                counter[sid] += 1
                item = {
                    "id": f"{PREFIX[sid]}{counter[sid]:02d}",
                    "section": sid,
                    "query": q,
                    "title": r["title"],
                    "url": url,
                    "domain": domain,
                    "date": r.get("date", ""),
                    "snippet": r.get("snippet", ""),
                    "text": "",
                    "tier": source_tier(domain, cfg["source_tiers"]),
                    "score": sc,
                    "keywords": hits,
                    "fetch_status": "not_fetched",
                }
                items.append(item)
                if picked < fetch_top and not any(b in domain for b in NO_FETCH_DOMAINS):
                    rec = web.fetch(url)
                    if rec.get("text"):
                        item["text"] = rec["text"][:6000]
                        item["fetch_status"] = f"ok:{rec['status']}"
                    else:
                        item["fetch_status"] = "fail:" + (rec.get("error") or str(rec.get("status")))
                    picked += 1
                    time.sleep(0.5)
        for it in items:
            d = parse_date(it["date"])
            it["in_window"] = bool(d and start <= d <= end)
            it["age_days"] = (end - d).days if d else None
        items.sort(key=lambda x: (-x["score"], x["date"] or ""))
        sections_out[sid] = items
    sections_out["_gaps"] = gaps
    return sections_out


def write_digest(sections, out_md, client, start, end):
    lines = [f"# 证据包：{client['short']} ESG 月报（{start} ~ {end}）", ""]
    sec_title = {s["id"]: s["title"] for s in client["report"]["sections"]}
    for sid, items in sections.items():
        lines.append(f"## {sec_title[sid]}（{len(items)} 条，其中本月 {sum(1 for i in items if i['in_window'])} 条）")
        for it in items:
            tag = "本月" if it["in_window"] else (f"{it['date']}" if it["date"] else "无日期")
            lines.append(f"- [{it['id']}] ({tag} | {it['domain']} | 分 {it['score']}) {it['title']}")
            if it["snippet"]:
                lines.append(f"  摘要: {it['snippet'][:200]}")
            if it["text"]:
                body = re.sub(r"\s+", " ", it["text"])[:420]
                lines.append(f"  正文: {body}")
            lines.append(f"  链接: {it['url']}")
        lines.append("")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", default="config/client_shenhua.yaml")
    ap.add_argument("--end", default=date.today().isoformat())
    ap.add_argument("--days", type=int, default=31)
    ap.add_argument("--num", type=int, default=8, help="results per query")
    ap.add_argument("--fetch-top", type=int, default=2, help="pages to fetch per query")
    ap.add_argument("--out", default="data/evidence.json")
    args = ap.parse_args()

    with open(args.client, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    client = {**raw["client"], "report": raw["report"]}
    cfg = raw["scoring"]
    end = datetime.strptime(args.end, "%Y-%m-%d").date()
    start = end - timedelta(days=args.days)

    sections = collect(client, cfg, start, end, args.num, args.fetch_top)
    gaps = sections.pop("_gaps", [])

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    payload = {
        "meta": {
            "client": client["name"],
            "short": client["short"],
            "period": f"{start} ~ {end}",
            "collected_at": datetime.now().isoformat(timespec="seconds"),
            "window_days": args.days,
            "query_count": sum(len(s["queries"]) for s in client["report"]["sections"]),
            "empty_queries": gaps,
        },
        "sections": sections,
    }
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    write_digest(sections, args.out.replace(".json", ".md"), client, start, end)

    stats = {
        "total": sum(len(v) for v in sections.values()),
        "in_window": sum(1 for v in sections.values() for i in v if i["in_window"]),
        "fetched_ok": sum(1 for v in sections.values() for i in v if i["fetch_status"].startswith("ok")),
        "by_section": {k: len(v) for k, v in sections.items()},
        "by_tier": {str(t): sum(1 for v in sections.values() for i in v if i["tier"] == t) for t in [3, 2, 1, 0]},
        "empty_queries": len(gaps),
    }
    if gaps:
        stats["gap_list"] = gaps
    with open(os.path.join(os.path.dirname(args.out), "collect_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
