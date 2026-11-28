#!/usr/bin/env python3
"""Deduplicate CSV records by conservative canonicalization of a URL column."""
import argparse
import csv
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

TRACKING_PREFIXES = ("utm_",)
TRACKING_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}

def canonicalize(url):
    parts = urlsplit((url or "").strip())
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname:
        return ""
    host = parts.hostname.lower()
    if parts.port and not ((parts.scheme.lower(), parts.port) in {("http", 80), ("https", 443)}):
        host += f":{parts.port}"
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in TRACKING_KEYS and not k.lower().startswith(TRACKING_PREFIXES)]
    query.sort()
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query, doseq=True), ""))

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("input", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--url-column", default="url")
    ap.add_argument("--duplicates-output", type=Path)
    args = ap.parse_args()
    with args.input.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        if args.url_column not in fields:
            ap.error(f"input is missing URL column: {args.url_column}")
        rows = list(reader)
    unique, duplicates, seen = [], [], {}
    for index, row in enumerate(rows, start=2):
        key = canonicalize(row.get(args.url_column, ""))
        row["canonical_url"] = key
        if not key:
            row["duplicate_of_row"] = ""
            unique.append(row)
        elif key in seen:
            row["duplicate_of_row"] = str(seen[key])
            duplicates.append(row)
        else:
            seen[key] = index
            row["duplicate_of_row"] = ""
            unique.append(row)
    output_fields = fields + ["canonical_url", "duplicate_of_row"]
    for path, data in ((args.output, unique), (args.duplicates_output, duplicates)):
        if path is None:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=output_fields)
            writer.writeheader(); writer.writerows(data)
    print(f"input={len(rows)} unique={len(unique)} duplicates={len(duplicates)}")

if __name__ == "__main__":
    main()
