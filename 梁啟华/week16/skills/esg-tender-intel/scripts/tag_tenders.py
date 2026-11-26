#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""招投标关键词打标、服务类型分类与噪声过滤（仅标准库）。

用法：
    python -I scripts/tag_tenders.py --input clean.csv --outdir out
    python -I scripts/tag_tenders.py --input clean.csv --min-score 4 --text-cols title,description

输出：
    out/tagged.csv        打标结果（相关性分、服务类型、噪声标记）
    out/tag_summary.json  统计摘要与规则快照

规则详见 references/keyword-library.md 与 references/service-taxonomy.md。
"""

import argparse
import csv
import json
import os
import re
import sys
import unicodedata

DERIVED = [
    "matched_tier1", "matched_tier2", "matched_tier3", "matched_keywords",
    "relevance_score", "service_type", "service_type_label",
    "service_subtypes", "noise_flag", "noise_reason",
]
DEFAULT_TEXT_COLS = ["title", "description", "需求", "采购需求", "scope", "content"]


def configure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def normalize(text):
    """NFKC + 去空白 + 小写，便于中英文统一子串匹配。"""
    text = unicodedata.normalize("NFKC", text if text is not None else "")
    return re.sub(r"\s+", "", text).lower()


def load_rules(path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def default_rules_path():
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "assets", "keyword-rules.json"))


def read_rows(path):
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8-sig") as f:
            return [dict(x) for x in json.load(f)]
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    return [dict(r) for r in csv.DictReader(lines)]


def hit_keywords(text, keywords):
    """返回命中的关键词（去重、保持词库顺序）。text 与关键词均已 normalize。"""
    hits = []
    for kw in keywords:
        nk = normalize(kw)
        if nk and nk in text and kw not in hits:
            hits.append(kw)
    return hits


def classify(text, service_types):
    """返回 (主标签 key, 中文名, 副标签中文列表)。"""
    scored = []
    for order, st in enumerate(service_types):
        hits = hit_keywords(text, st.get("keywords", []))
        if hits:
            scored.append((len(hits), order, st, hits))
    if not scored:
        return "other", "其他/待定", []
    scored.sort(key=lambda x: (-x[0], x[1]))
    primary = scored[0]
    subtypes = [s[2]["label"] for s in scored[1:4]]
    return primary[2]["key"], primary[2]["label"], subtypes


def build_text(row, text_cols):
    parts = []
    for col in text_cols:
        val = row.get(col)
        if val:
            parts.append(str(val))
    return normalize(" ".join(parts))


def evaluate(row, rules, text_cols, min_score):
    text = build_text(row, text_cols)
    tiers = rules["tiers"]

    tier_hits = {}
    score = 0
    for name, spec in tiers.items():
        hits = hit_keywords(text, spec.get("keywords", []))
        tier_hits[name] = hits
        if hits:
            score += int(spec.get("weight", 0))

    exclusions = rules.get("exclusions", {})
    ex_hits = hit_keywords(text, exclusions.get("keywords", []))
    if ex_hits:
        score += int(exclusions.get("weight", 0))

    has_core = bool(tier_hits.get("tier1") or tier_hits.get("tier2"))
    require_non_tier3 = rules.get("rules", {}).get("require_non_tier3", True)

    reasons = []
    if score < min_score:
        reasons.append("score<%d" % min_score)
    if require_non_tier3 and not has_core:
        reasons.append("仅命中三级服务词或未命中")
    if ex_hits:
        reasons.append("命中排除词:" + "、".join(ex_hits))
    noise = bool(reasons)

    service_type, service_label, subtypes = classify(text, rules.get("service_types", []))
    all_hits = []
    for name in ("tier1", "tier2", "tier3"):
        all_hits.extend(tier_hits.get(name, []))

    return {
        "matched_tier1": "|".join(tier_hits.get("tier1", [])),
        "matched_tier2": "|".join(tier_hits.get("tier2", [])),
        "matched_tier3": "|".join(tier_hits.get("tier3", [])),
        "matched_keywords": "|".join(all_hits),
        "relevance_score": score,
        "service_type": service_type,
        "service_type_label": service_label,
        "service_subtypes": "|".join(subtypes),
        "noise_flag": "true" if noise else "false",
        "noise_reason": "；".join(reasons),
    }


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description="招投标关键词打标与噪声过滤")
    parser.add_argument("--input", required=True, help="输入 CSV/JSON（建议使用 normalize_dedup 输出）")
    parser.add_argument("--outdir", default=".", help="输出目录")
    parser.add_argument("--rules", default=default_rules_path(), help="关键词规则 JSON 路径")
    parser.add_argument("--min-score", type=int, default=None, help="相关性阈值（默认取规则文件）")
    parser.add_argument("--text-cols", default=None, help="参与匹配的列，逗号分隔；默认 title + 可能存在的描述列")
    args = parser.parse_args()

    rules = load_rules(args.rules)
    min_score = args.min_score if args.min_score is not None else int(rules["rules"]["min_relevance_score"])

    rows = read_rows(args.input)
    if not rows:
        raise SystemExit("输入文件为空：%s" % args.input)

    available = set(rows[0].keys())
    if args.text_cols:
        text_cols = [c.strip() for c in args.text_cols.split(",") if c.strip()]
    else:
        text_cols = [c for c in DEFAULT_TEXT_COLS if c in available]
    if not text_cols:
        raise SystemExit("未找到可用文本列，请用 --text-cols 指定。")

    results = [evaluate(row, rules, text_cols, min_score) for row in rows]

    os.makedirs(args.outdir, exist_ok=True)
    out_path = os.path.join(args.outdir, "tagged.csv")
    summary_path = os.path.join(args.outdir, "tag_summary.json")

    fieldnames = []
    for row in rows:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    for col in DERIVED:
        if col not in fieldnames:
            fieldnames.append(col)

    with open(out_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row, res in zip(rows, results):
            out = dict(row)
            out.update(res)
            writer.writerow(out)

    # 统计
    service_counts = {}
    for res in results:
        if res["noise_flag"] == "false":
            service_counts[res["service_type_label"]] = service_counts.get(res["service_type_label"], 0) + 1
    summary = {
        "rules_version": rules.get("version"),
        "input_rows": len(rows),
        "relevant_rows": sum(1 for r in results if r["noise_flag"] == "false"),
        "noise_rows": sum(1 for r in results if r["noise_flag"] == "true"),
        "service_type_counts": dict(sorted(service_counts.items(), key=lambda kv: -kv[1])),
        "min_score": min_score,
        "text_cols": text_cols,
        "keyword_scope": {
            "tier1": len(rules["tiers"]["tier1"]["keywords"]),
            "tier2": len(rules["tiers"]["tier2"]["keywords"]),
            "tier3": len(rules["tiers"]["tier3"]["keywords"]),
            "exclusions": len(rules.get("exclusions", {}).get("keywords", [])),
        },
    }
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # 关键词范围披露（对应质量门 keyword_scope_disclosed）
    print("=" * 60)
    print("关键词范围：L1 %d 个、L2 %d 个、L3 %d 个（版本 %s）；排除词 %d 个"
          % (summary["keyword_scope"]["tier1"], summary["keyword_scope"]["tier2"],
             summary["keyword_scope"]["tier3"], rules.get("version"), summary["keyword_scope"]["exclusions"]))
    print("匹配字段：%s；相关性阈值：%d 分" % ("+".join(text_cols), min_score))
    print("输入 %d 条 -> 相关 %d 条，噪声 %d 条"
          % (summary["input_rows"], summary["relevant_rows"], summary["noise_rows"]))
    if summary["service_type_counts"]:
        print("服务类型分布（仅相关项目）：")
        for label, count in summary["service_type_counts"].items():
            print("  - %-24s %d" % (label, count))
    print("输出：%s" % out_path)
    print("输出：%s" % summary_path)


if __name__ == "__main__":
    main()
