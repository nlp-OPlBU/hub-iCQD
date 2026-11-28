"""Run a bounded batch of public Brave Web Search queries."""

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research_common import iso_date, read_json, write_json

ENDPOINT = "https://api.search.brave.com/res/v1/web/search"


def load_queries(path):
    queries = read_json(path)
    if not isinstance(queries, list) or not 1 <= len(queries) <= 32:
        raise ValueError("Provide a list containing 1 to 32 queries.")
    rows = []
    for row in queries:
        if not isinstance(row, dict):
            raise ValueError("Each query must be an object with group and query fields.")
        group, query = row.get("group"), row.get("query")
        if not isinstance(group, str) or not group.strip():
            raise ValueError("Each query needs a nonempty group.")
        if not isinstance(query, str) or not query.strip() or len(query) > 600 or len(query.split()) > 75:
            raise ValueError("Each query needs 1 to 600 characters and at most 75 words.")
        rows.append((group.strip(), query.strip()))
    return list(dict.fromkeys(rows))


def fetch(query, options, token):
    params = {
        "q": query, "count": options.count,
        "text_decorations": "false", "result_filter": "web",
    }
    for flag in ("country", "search_lang", "freshness"):
        if value := getattr(options, flag):
            params[flag] = value
    request = Request(f"{ENDPOINT}?{urlencode(params)}", headers={"Accept": "application/json"})
    request.add_unredirected_header("X-Subscription-Token", token)
    with urlopen(request, timeout=options.timeout) as response:
        payload = json.load(response)
    if not isinstance(payload, dict) or "error" in payload:
        raise ValueError("Unexpected provider response.")
    web = payload.get("web") or {}
    return [
        {
            "title": unescape(hit.get("title") or ""),
            "url": hit["url"],
            "snippet": unescape(hit.get("description") or ""),
            "date_hint": hit.get("page_age") or hit.get("age") or "",
        }
        for hit in web.get("results", []) if hit.get("url")
    ]


def safe_fetch(query, options, token):
    try:
        return {"results": fetch(query, options, token)}
    except HTTPError as exc:
        exc.close()
        return {"results": [], "error": f"HTTP {exc.code}"}
    except (URLError, TimeoutError, OSError, ValueError, AttributeError, TypeError):
        return {"results": [], "error": "Network failure or invalid provider response"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--count", type=int, default=8, choices=range(1, 21), metavar="1–20")
    parser.add_argument("--workers", type=int, default=2, choices=range(1, 9), metavar="1–8")
    parser.add_argument("--timeout", type=int, default=15, metavar="SECONDS")
    parser.add_argument("--country")
    parser.add_argument("--search-lang")
    parser.add_argument("--freshness", help="pd, pw, pm, py, or YYYY-MM-DDtoYYYY-MM-DD")
    options = parser.parse_args()

    try:
        if options.timeout <= 0:
            raise ValueError("--timeout must be positive.")
        if options.freshness and options.freshness not in {"pd", "pw", "pm", "py"}:
            dates = options.freshness.split("to")
            if len(dates) != 2 or iso_date(dates[0]) > iso_date(dates[1]):
                raise ValueError("--freshness requires two ordered dates.")
        rows = load_queries(options.queries)
        token = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip()
        if not token:
            raise ValueError("BRAVE_SEARCH_API_KEY is unset. Use the built-in web tool or set the key.")
        queries = list(dict.fromkeys(query for _, query in rows))
        with ThreadPoolExecutor(max_workers=options.workers) as pool:
            responses = pool.map(lambda query: safe_fetch(query, options, token), queries)
            results = dict(zip(queries, responses))
        runs = [{"group": group, "query": query, **results[query]} for group, query in rows]
        write_json(options.output, {"runs": runs})
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    errors = sum("error" in result for result in results.values())
    print(f"{len(queries)} distinct queries, {errors} failed. Results written to {options.output}.")
    return int(errors == len(queries))


if __name__ == "__main__":
    raise SystemExit(main())
