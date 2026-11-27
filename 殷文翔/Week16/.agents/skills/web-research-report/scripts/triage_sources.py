"""Deduplicate, rank, and date-filter search candidates by research group."""

import argparse
from pathlib import Path
from urllib.parse import unquote_plus, urlunsplit

from research_common import iso_date, read_json, web_url, write_json

TRACKING_KEYS = {"fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid"}


def canonical_url(value):
    parts = web_url(value)
    query = []
    for parameter in parts.query.split("&"):
        name = unquote_plus(parameter.partition("=")[0]).casefold()
        if name not in TRACKING_KEYS and not name.startswith("utm_"):
            query.append(parameter)
    fragment = parts.fragment if parts.fragment.startswith(("/", "!")) else ""
    return urlunsplit(
        (parts.scheme, parts.netloc.lower(), parts.path or "/", "&".join(query), fragment)
    )


def collect(runs):
    groups = {}
    rejected = 0
    errors = []
    for run in runs:
        group, query = run["group"], run["query"]
        candidates = groups.setdefault(group, {})
        seen = set()
        if error := run.get("error"):
            errors.append({"group": group, "query": query, "error": error})
        for position, hit in enumerate(run["results"], start=1):
            try:
                key = canonical_url(hit.get("url"))
            except ValueError:
                rejected += 1
                continue
            record = candidates.setdefault(key, {
                "url": key,
                "original_url": hit["url"],
                "title": "",
                "snippet": "",
                "dates": set(),
                "date_hints": set(),
                "positions": {},
            })
            for field in ("title", "snippet"):
                if len(value := hit.get(field) or "") > len(record[field]):
                    record[field] = value
            if hint := hit.get("date_hint"):
                record["date_hints"].add(hint)
            if published := hit.get("published"):
                record["dates"].add(iso_date(published))
            if key not in seen:
                positions = record["positions"]
                positions[query] = min(position, positions.get(query, position))
                seen.add(key)
    return groups, rejected, errors


def shortlist(candidates, start, end, preferred_domains, limit):
    ranked = []
    outside = []
    for record in candidates.values():
        dates = record.pop("dates")
        published = next(iter(dates)) if len(dates) == 1 else None
        if published:
            record["published"] = published.isoformat()
        if len(dates) > 1:
            record["conflicting_dates"] = sorted(value.isoformat() for value in dates)
        record["date_hints"] = sorted(record["date_hints"])
        hostname = web_url(record["url"]).hostname.lower().rstrip(".")
        preferred = any(hostname == domain or hostname.endswith("." + domain) for domain in preferred_domains)
        positions = record.pop("positions")
        record["queries"] = list(positions)
        rank = sum(1 / (10 + position) for position in positions.values())
        record["score"] = round(rank * (1.15 if preferred else 1), 6)
        if published and ((start and published < start) or (end and published > end)):
            outside.append(record)
        else:
            ranked.append(record)
    ranked.sort(key=lambda record: (-record["score"], record["url"]))
    return {
        "candidates": ranked[:limit],
        "eligible_total": len(ranked),
        "outside_window": sorted(outside, key=lambda record: record["url"]),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=iso_date)
    parser.add_argument("--end", type=iso_date)
    parser.add_argument("--limit", type=int, default=8, help="Maximum candidates per group")
    parser.add_argument("--prefer-domain", action="append", default=[])
    options = parser.parse_args()
    try:
        if options.limit < 1 or (options.start and options.end and options.start > options.end):
            raise ValueError("Provide a positive limit and an ordered time window.")
        runs = read_json(options.input)["runs"]
        groups, rejected, errors = collect(runs)
        domains = [domain.lower().strip(".") for domain in options.prefer_domain if domain]
        output = {
            "groups": {
                name: shortlist(hits, options.start, options.end, domains, options.limit)
                for name, hits in groups.items()
            },
            "invalid_urls": rejected,
            "search_errors": errors,
        }
        write_json(options.output, output)
    except (OSError, KeyError, TypeError, AttributeError, ValueError) as exc:
        parser.error(f"Unable to process candidate data. {exc}")


if __name__ == "__main__":
    main()
