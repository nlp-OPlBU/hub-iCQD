#!/usr/bin/env python3
"""Normalize common Chinese/ISO dates in selected CSV columns to YYYY-MM-DD."""
import argparse
import csv
import re
from datetime import date, datetime
from pathlib import Path

PATTERNS = [
    re.compile(r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?$"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日$"),
]

def normalize(value):
    value = (value or "").strip()
    if not value:
        return "", "empty"
    candidate = value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}[T ]", value) else value
    for pattern in PATTERNS:
        match = pattern.fullmatch(candidate)
        if match:
            try:
                return date(*(int(x) for x in match.groups())).isoformat(), ""
            except ValueError:
                return "", "invalid_calendar_date"
    return "", "unsupported_or_ambiguous_format"

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--columns", nargs="+", required=True, help="CSV date column names")
    args = ap.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        missing = [c for c in args.columns if c not in fields]
        if missing:
            ap.error("input is missing columns: " + ", ".join(missing))
        rows = list(reader)
    for column in args.columns:
        fields.extend([column + "_iso", column + "_date_error"])
    for row in rows:
        for column in args.columns:
            row[column + "_iso"], row[column + "_date_error"] = normalize(row.get(column, ""))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader(); writer.writerows(rows)
    errors = sum(bool(row[c + "_date_error"]) and row[c + "_date_error"] != "empty"
                 for row in rows for c in args.columns)
    print(f"rows={len(rows)} date_errors={errors} output={args.output}")

if __name__ == "__main__":
    main()
