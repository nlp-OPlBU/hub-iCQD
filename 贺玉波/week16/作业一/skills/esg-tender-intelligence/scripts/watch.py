#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Daily watch: incremental scan of new ESG / dual-carbon tender notices.

State lives in data/seen.json (URLs + project keys), so each run only reports what is new.
Designed to be cron-able:
  0 9 * * *  cd <skill> && python3 scripts/watch.py --days 2 --push-file out/daily.md

Outputs:
  data/watch/YYYY-MM-DD.md     daily digest (rendered, ready to paste into IM/email)
  data/seen.json               updated state
  data/tenders_watch.jsonl     append-only log of detected notices
"""
import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import web     # noqa: E402
import extract  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_seen(p):
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(p, seen):
    with open(p, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=0)


def key_of(r):
    return re.sub(r"\s+", "", (r.get("project") or r.get("title") or ""))[:30] + "|" + str(r.get("amount_wan") or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(HERE, "config/queries.yaml"))
    ap.add_argument("--days", type=int, default=2)
    ap.add_argument("--num", type=int, default=10)
    ap.add_argument("--fetch-max", type=int, default=25)
    ap.add_argument("--seen", default=os.path.join(HERE, "data/seen.json"))
    ap.add_argument("--out-dir", default=os.path.join(HERE, "data/watch"))
    ap.add_argument("--push-file", default="")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    today = date.today()
    since = today - timedelta(days=args.days)
    seen = load_seen(args.seen)
    filters, dom = cfg["filters"], cfg["domains"]
    cat_names = {c["id"]: c["name"] for c in cfg["categories"]}

    new_rows = []
    fetched = 0
    watch_queries = cfg.get("watch_queries") or [
        c["queries"][0] + f" {today.year}年{today.month}月" for c in cfg["categories"]]
    for wq in watch_queries:
        q = wq
        cat = {"id": "watch", "name": "监控"}
        try:
            results = web.search(q, num=args.num)
        except Exception as e:
            sys.stderr.write(f"[watch] search err {q}: {e}\n")
            continue
        for r in results:
            text = r["title"] + " " + r.get("snippet", "")
            if not any(k in text for k in filters["must_match"]):
                continue
            if any(k in text for k in filters["exclude"]):
                continue
            d = web.domain_of(r["url"])
            if any(d.endswith(b) or b in d for b in dom["block"]):
                continue
            dt = r.get("date") or extract.find_date(text)
            if dt:
                try:
                    if datetime.strptime(dt, "%Y-%m-%d").date() < since:
                        continue
                except Exception:
                    pass
            body = r.get("snippet", "")
            status = "snippet_only"
            if fetched < args.fetch_max:
                rec = web.fetch(r["url"])
                fetched += 1
                if rec.get("text"):
                    body, status = rec["text"], f"ok:{rec['status']}"
            row = extract.extract(r["title"], body, r["url"])
            hint = ("platform" if ("碳管理平台" in q or "碳足迹" in q) else
                    "carbonasset" if "CCER" in q else
                    "report" if "报告" in q else
                    "consulting" if "咨询" in q else
                    "zerocarbon" if "零碳" in q else
                    "dualcarbon")
            row["category"] = row["category"] if row["category"] != "other" else hint
            row["category_name"] = cat_names.get(row["category"], row["category"])
            row["date"] = row["date"] or dt
            row["page_status"] = status
            k = key_of(row)
            if r["url"] in seen or k in seen:
                continue
            seen.add(r["url"])
            seen.add(k)
            row["detected_at"] = datetime.now().isoformat(timespec="seconds")
            new_rows.append(row)

    os.makedirs(args.out_dir, exist_ok=True)
    stamp = today.isoformat()
    md_path = os.path.join(args.out_dir, f"{stamp}.md")
    lines = [f"# ESG 招标日报 {stamp}", ""]
    if not new_rows:
        lines.append("今日无新增匹配的 ESG / 双碳相关中标信息。")
    else:
        lines.append(f"共发现 **{len(new_rows)}** 条新增中标/成交信息，按金额排序：\n")
        new_rows.sort(key=lambda r: -(r["amount_wan"] or 0))
        for i, r in enumerate(new_rows, 1):
            amt = f"{r['amount_wan']:,.2f} 万元" if r["amount_wan"] else "金额未披露"
            lines.append(f"**{i}. {r['project']}**")
            lines.append(f"- 金额：{amt}　|　日期：{r['date'] or '未标注'}　|　赛道：{r['category_name']}")
            if r["buyer"]:
                lines.append(f"- 采购人：{r['buyer']}")
            if r["supplier"]:
                lines.append(f"- 中标方：{r['supplier']}")
            lines.append(f"- 来源：{r['url']}")
            lines.append("")
    lines.append("---")
    lines.append(f"扫描窗口：{since} ~ {today}　|　新增 {len(new_rows)} 条　|　"
                 f"数据来源：公开招标公告检索，金额以公告原文为准")
    md = "\n".join(lines)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(md)
    if args.push_file:
        os.makedirs(os.path.dirname(args.push_file) or ".", exist_ok=True)
        with open(args.push_file, "w", encoding="utf-8") as f:
            f.write(md)

    if new_rows:
        os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
        with open(os.path.join(HERE, "data", "tenders_watch.jsonl"), "a", encoding="utf-8") as f:
            for r in new_rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    save_seen(args.seen, seen)
    print(f"{stamp}: {len(new_rows)} new -> {md_path}")
    print(md)


if __name__ == "__main__":
    main()
