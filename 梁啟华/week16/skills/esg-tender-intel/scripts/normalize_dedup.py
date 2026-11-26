#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""招投标数据归一化、去重与金额口径统一（仅标准库）。

用法：
    python -I scripts/normalize_dedup.py --input raw.csv --outdir out
    python -I scripts/normalize_dedup.py --input raw.json --outdir out --id-col 项目编号

输出：
    out/clean.csv          清洗后的数据（附 amount_wan / amount_basis / dedup_key / source_rows）
    out/dedup_report.json  去重与金额统计报告

规则详见 references/dedup-and-amount.md。
"""

import argparse
import csv
import json
import os
import re
import statistics
import sys
import unicodedata

SEPARATOR_RE = re.compile(r"[\s\-_/\\（）()【】\[\]、]+")
AMOUNT_RE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(亿元|亿|万元|万|元)?$")

# 默认字段名（可通过命令行覆盖）
DEFAULTS = {
    "id": "project_id",
    "title": "title",
    "buyer": "buyer",
    "date": "publish_date",
    "award": "award_amount",
    "contract": "contract_amount",
    "budget": "budget_amount",
    "supplier": "supplier",
    "source": "source_url",
}

DERIVED_COLUMNS = ["amount_wan", "amount_basis", "dedup_key", "source_rows"]


def configure_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def nfkc(text):
    return unicodedata.normalize("NFKC", text if text is not None else "")


def norm_id(raw):
    """项目编号归一化：NFKC -> 去空白与分隔符 -> 大写。"""
    return SEPARATOR_RE.sub("", nfkc(raw).strip()).upper()


def norm_text(raw):
    return re.sub(r"\s+", "", nfkc(raw).strip()).lower()


def parse_amount(raw):
    """解析金额，返回 (万元, 原始单位, 是否推断单位)；无法解析返回 (None, None, False)。"""
    if raw is None:
        return None, None, False
    text = nfkc(str(raw)).strip()
    if not text or text in ("-", "--", "无", "null", "None"):
        return None, None, False
    text = re.sub(r"[￥¥,，\s]", "", text)
    text = text.replace("人民币", "")
    m = AMOUNT_RE.match(text)
    if not m:
        return None, None, False
    value = float(m.group(1))
    unit = m.group(2)
    if unit in ("亿元", "亿"):
        return round(value * 10000, 2), "亿元", False
    if unit in ("万元", "万"):
        return round(value, 2), "万元", False
    if unit == "元":
        return round(value / 10000, 2), "元", False
    # 无单位：保守按“元”处理，并标记为推断
    return round(value / 10000, 2), "元(推断)", True


def pick_amount(row, cols):
    """按 中标 > 合同 > 预算 的优先级取金额。"""
    for col, basis in ((cols["award"], "award"), (cols["contract"], "contract"), (cols["budget"], "budget")):
        if col and col in row:
            wan, unit, inferred = parse_amount(row.get(col))
            if wan is not None:
                return wan, basis, unit, inferred
    return None, "unknown", None, False


def read_rows(path):
    """读取 CSV 或 JSON；CSV 跳过以 # 开头的注释行。"""
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise SystemExit("JSON 输入必须是对象数组。")
        return [dict(item) for item in data]

    rows = []
    with open(path, "r", encoding="utf-8-sig", newline="") as f:
        lines = [ln for ln in f if not ln.lstrip().startswith("#")]
    reader = csv.DictReader(lines)
    for row in reader:
        rows.append(dict(row))
    return rows


def completeness_score(row, cols):
    """字段完整度打分，用于在重复组中保留信息更全的记录。"""
    score = sum(1 for k, v in row.items() if k not in DERIVED_COLUMNS and str(v or "").strip())
    if str(row.get(cols["award"], "") or "").strip():
        score += 2
    if str(row.get(cols["supplier"], "") or "").strip():
        score += 1
    if "gov" in str(row.get(cols["source"], "") or "").lower():
        score += 1
    return score


def main():
    configure_stdout()
    parser = argparse.ArgumentParser(description="招投标数据归一化 + 去重 + 金额统一")
    parser.add_argument("--input", required=True, help="输入 CSV/JSON 文件")
    parser.add_argument("--outdir", default=".", help="输出目录（默认当前目录）")
    parser.add_argument("--id-col", default=DEFAULTS["id"])
    parser.add_argument("--title-col", default=DEFAULTS["title"])
    parser.add_argument("--buyer-col", default=DEFAULTS["buyer"])
    parser.add_argument("--date-col", default=DEFAULTS["date"])
    parser.add_argument("--award-col", default=DEFAULTS["award"])
    parser.add_argument("--contract-col", default=DEFAULTS["contract"])
    parser.add_argument("--budget-col", default=DEFAULTS["budget"])
    parser.add_argument("--supplier-col", default=DEFAULTS["supplier"])
    parser.add_argument("--source-col", default=DEFAULTS["source"])
    args = parser.parse_args()

    cols = {
        "id": args.id_col, "title": args.title_col, "buyer": args.buyer_col,
        "date": args.date_col, "award": args.award_col, "contract": args.contract_col,
        "budget": args.budget_col, "supplier": args.supplier_col, "source": args.source_col,
    }

    rows = read_rows(args.input)
    total_in = len(rows)

    records = []
    for idx, row in enumerate(rows):
        raw_id = row.get(cols["id"], "")
        key = norm_id(raw_id)
        composite = False
        if not key:
            date = nfkc(str(row.get(cols["date"], "") or "")).strip()[:7]
            key = "T|%s|%s|%s" % (
                norm_text(row.get(cols["title"], "")),
                norm_text(row.get(cols["buyer"], "")),
                date,
            )
            composite = True

        # 幂等：优先复用已清洗文件中的 amount_wan / amount_basis
        existing_wan = str(row.get("amount_wan", "") or "").strip()
        if existing_wan:
            try:
                amount_wan = round(float(existing_wan), 2)
                amount_basis = str(row.get("amount_basis", "unknown") or "unknown").strip()
                unit, inferred = None, False
            except ValueError:
                amount_wan, amount_basis, unit, inferred = pick_amount(row, cols)
        else:
            amount_wan, amount_basis, unit, inferred = pick_amount(row, cols)

        records.append({
            "index": idx,
            "row": row,
            "key": key,
            "composite": composite,
            "amount_wan": amount_wan,
            "amount_basis": amount_basis,
            "unit": unit,
            "unit_inferred": inferred,
            "score": completeness_score(row, cols),
        })

    # 稳定去重：按 (key, index) 排序后，同 key 保留完整度最高者（并列取最靠前者）
    groups = {}
    for rec in sorted(records, key=lambda r: (r["key"], r["index"])):
        groups.setdefault(rec["key"], []).append(rec)

    kept = []
    duplicate_groups = []
    dropped_count = 0
    for key in sorted(groups):
        members = groups[key]
        best = max(members, key=lambda r: (r["score"], -r["index"]))
        kept.append(best)
        if len(members) > 1:
            others = [m for m in members if m is not best]
            dropped_count += len(others)
            duplicate_groups.append({
                "dedup_key": key,
                "kept": str(best["row"].get(cols["id"], "") or best["row"].get(cols["title"], "")),
                "dropped": [str(m["row"].get(cols["id"], "") or m["row"].get(cols["title"], "")) for m in others],
                "reason": "completeness_score=%d" % best["score"],
            })

    # 按原始顺序输出保留记录
    kept.sort(key=lambda r: r["index"])

    # 汇总金额
    amounts = [r["amount_wan"] for r in kept if r["amount_wan"] is not None]
    basis_counts = {"award": 0, "contract": 0, "budget": 0, "unknown": 0}
    for rec in kept:
        basis_counts[rec["amount_basis"]] = basis_counts.get(rec["amount_basis"], 0) + 1

    report = {
        "total_in": total_in,
        "unique_out": len(kept),
        "duplicate_rows_removed": dropped_count,
        "duplicate_rate": round(dropped_count / total_in, 4) if total_in else 0.0,
        "duplicate_groups": duplicate_groups,
        "composite_key_count": sum(1 for r in kept if r["composite"]),
        "unit_inferred_count": sum(1 for r in kept if r["unit_inferred"]),
        "amount_basis_counts": basis_counts,
        "amount_stats": {
            "known": len(amounts),
            "unknown": len(kept) - len(amounts),
            "coverage": round(len(amounts) / len(kept), 4) if kept else 0.0,
            "total_wan": round(sum(amounts), 2) if amounts else 0.0,
            "min_wan": round(min(amounts), 2) if amounts else None,
            "max_wan": round(max(amounts), 2) if amounts else None,
            "median_wan": round(statistics.median(amounts), 2) if amounts else None,
        },
        "notes": [
            "金额统一为万元（保留 2 位小数）",
            "金额口径优先级：中标 > 合同 > 预算",
            "纯数字无单位按元处理，并计入 unit_inferred_count",
        ],
    }

    os.makedirs(args.outdir, exist_ok=True)
    clean_path = os.path.join(args.outdir, "clean.csv")
    report_path = os.path.join(args.outdir, "dedup_report.json")

    # 输出列：保留原始列 + 派生列（若原始已含派生列则不重复）
    fieldnames = []
    for row in rows:
        for k in row.keys():
            if k not in fieldnames:
                fieldnames.append(k)
    for col in DERIVED_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    with open(clean_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for rec in kept:
            out = dict(rec["row"])
            out["amount_wan"] = "" if rec["amount_wan"] is None else "%.2f" % rec["amount_wan"]
            out["amount_basis"] = rec["amount_basis"]
            out["dedup_key"] = rec["key"]
            out["source_rows"] = 1
            writer.writerow(out)

    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print("归一化去重完成")
    print("输入 %d 条 -> 去重后 %d 条，移除重复 %d 条（重复率 %.2f%%）"
          % (total_in, len(kept), dropped_count, report["duplicate_rate"] * 100))
    print("重复组 %d 个；组合键去重 %d 条；推算单位 %d 条"
          % (len(duplicate_groups), report["composite_key_count"], report["unit_inferred_count"]))
    stats = report["amount_stats"]
    print("金额：已知 %d 条（覆盖 %.1f%%），合计 %.2f 万元，中位数 %s 万元"
          % (stats["known"], stats["coverage"] * 100, stats["total_wan"], stats["median_wan"]))
    print("输出：%s" % clean_path)
    print("输出：%s" % report_path)


if __name__ == "__main__":
    main()
