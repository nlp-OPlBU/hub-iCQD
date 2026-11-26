#!/usr/bin/env python3
"""Discover web candidates online or replay captured provider responses."""

import argparse
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree


PROVIDERS = {
    "google-news": ("https://news.google.com/rss/search", "xml"),
    "bing-rss": ("https://www.bing.com/search", "xml"),
    "brave": ("https://api.search.brave.com/res/v1/web/search", "json"),
}
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
TRACKING_PARAMS = {"fbclid", "gclid", "msclkid"}


class SearchError(Exception):
    """An expected transport or response error."""


class SnippetParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value):
    if not isinstance(value, str):
        return ""
    parser = SnippetParser()
    parser.feed(value)
    parser.close()
    return " ".join("".join(parser.parts).split())


def url_key(url):
    """Return a comparison key, preserving the provider's original link in output."""
    try:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username is not None:
            return ""
        kept = [
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
        ]
        return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", urlencode(sorted(kept)), ""))
    except (TypeError, ValueError):
        return ""


def is_aggregator(url):
    parts = urlsplit(url)
    return parts.hostname == "news.google.com" or (
        parts.hostname in {"www.bing.com", "cn.bing.com"} and
        parts.path.startswith(("/news/", "/ck/", "/search"))
    )


def iso_date(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        try:
            date = parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None
    if date.tzinfo is None:
        return None
    return date.astimezone(timezone.utc).isoformat(timespec="seconds")


def make_request(provider, query, limit, token, freshness):
    endpoint, file_type = PROVIDERS[provider]
    params = {"q": query}
    accept = "application/json" if file_type == "json" else "application/rss+xml, application/xml, text/xml"
    if provider == "google-news":
        params.update(hl="zh-CN", gl="CN", ceid="CN:zh-Hans")
    elif provider == "bing-rss":
        params.update(format="rss", mkt="zh-CN", setlang="zh-CN")
    else:
        params.update(count=limit, result_filter="web", text_decorations="false")
        if freshness:
            params["freshness"] = freshness

    request = Request(endpoint + "?" + urlencode(params), headers={
        "Accept": accept,
        "User-Agent": "ESG-Research-Skill/1.0",
    })
    if provider == "brave":
        # urllib does not copy this header if the provider redirects the request.
        request.add_unredirected_header("X-Subscription-Token", token)
    return request


def download(request, timeout):
    try:
        with urlopen(request, timeout=timeout) as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        if error.code == 429:
            delay = error.headers.get("Retry-After")
            detail = f"，Retry-After={delay}" if delay and delay.isdigit() else ""
            raise SearchError(f"HTTP 429，提供方限制请求频率{detail}") from None
        if error.code in {401, 403}:
            raise SearchError(f"HTTP {error.code}，访问被拒绝，请检查 API 权限或提供方状态") from None
        raise SearchError(f"HTTP {error.code}，提供方请求失败") from None
    except (URLError, TimeoutError, OSError) as error:
        detail = error.reason if isinstance(error, URLError) else error
        raise SearchError(f"联网失败（{type(detail).__name__}） {detail}") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise SearchError("响应超过 4 MiB")
    return data


def parse_rss(data):
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise SearchError("RSS 响应包含不支持的文档声明")
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError:
        raise SearchError("提供方返回无效 RSS，可能是错误页或验证页") from None
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SearchError("提供方响应缺少 RSS channel")

    for item in channel.findall("item"):
        source = item.find("source")
        yield {
            "title": plain_text(item.findtext("title")),
            "url": (item.findtext("link") or "").strip(),
            "description": plain_text(item.findtext("description")),
            "published_at": iso_date(item.findtext("pubDate")),
            "publisher": plain_text(source.text) if source is not None else "",
            "publisher_url": (source.get("url") or "").strip() if source is not None else "",
        }


def parse_brave(data):
    try:
        root = json.loads(data)
    except (ValueError, UnicodeError):
        raise SearchError("Brave 响应不是有效 JSON") from None
    if not isinstance(root, dict) or root.get("type") not in {"search", "web"}:
        raise SearchError("Brave 响应不是 Web Search 结果，请检查 API 状态")
    web = root.get("web", {})
    if not isinstance(web, dict) or not isinstance(web.get("results", []), list):
        raise SearchError("Brave 响应的 web.results 格式错误")

    for item in web.get("results", []):
        if not isinstance(item, dict):
            continue
        url = item.get("url", "")
        yield {
            "title": plain_text(item.get("title")),
            "url": url.strip() if isinstance(url, str) else "",
            "description": plain_text(item.get("description")),
            "provider_date": item.get("page_age") if isinstance(item.get("page_age"), str) else None,
        }


def read_response(path):
    try:
        with path.open("rb") as response:
            data = response.read(MAX_RESPONSE_BYTES + 1)
    except OSError as error:
        raise SearchError(f"无法读取 {path.name}（{error.strerror}）") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise SearchError(f"{path.name} 超过 4 MiB")
    return data


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".esg-search-", delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def run_search(args, queries, token):
    retrieved_at = args.captured_at if args.response_dir else datetime.now(timezone.utc).isoformat(timespec="seconds")
    results = {}
    errors = []
    runs = []
    _, file_type = PROVIDERS[args.provider]
    for index, query in enumerate(queries, start=1):
        file_name = f"response-{index:02d}.{file_type}"
        try:
            if args.response_dir:
                data = read_response(args.response_dir / file_name)
            else:
                request = make_request(args.provider, query, args.limit, token, args.freshness)
                data = download(request, args.timeout)
                if args.capture_dir:
                    write_atomic(args.capture_dir / file_name, data)
            entries = list(parse_brave(data) if args.provider == "brave" else parse_rss(data))
        except (SearchError, OSError) as error:
            errors.append({"query": query, "message": str(error)})
            continue

        kept = 0
        query_keys = set()
        for rank, entry in enumerate(entries, start=1):
            key = url_key(entry["url"])
            if not key or not entry["title"] or key in query_keys:
                continue
            query_keys.add(key)
            kept += 1
            if key in results:
                results[key]["queries"].append(query)
            else:
                record = {field: value for field, value in entry.items() if value or field == "description"}
                record.update(query=query, queries=[query], rank=rank, is_aggregator=is_aggregator(entry["url"]))
                results[key] = record
            if kept == args.limit:
                break
        runs.append({"query": query, "provider_results": len(entries), "kept": kept})

    status = "failed" if not runs else "partial" if errors else "ok"
    return {
        "provider": args.provider,
        "mode": "replay" if args.response_dir else "online",
        "retrieved_at": retrieved_at,
        "status": status,
        "queries": queries,
        "query_runs": runs,
        "results": list(results.values()),
        "errors": errors,
    }


def arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=PROVIDERS, default="google-news")
    parser.add_argument("--query", action="append", default=[], help="repeatable")
    parser.add_argument("--query-file", type=Path, help="UTF-8 JSON array of query strings")
    parser.add_argument("--limit", type=int, default=8, choices=range(1, 21), metavar="1..20")
    parser.add_argument("--timeout", type=int, default=15, choices=range(1, 61), metavar="1..60")
    parser.add_argument("--freshness", help="Brave only: pd, pw, pm, py, or YYYY-MM-DDtoYYYY-MM-DD")
    parser.add_argument("--out", default="-", help="JSON output file or - for stdout")
    capture = parser.add_mutually_exclusive_group()
    capture.add_argument("--capture-dir", type=Path, help="save real HTTP responses for later replay")
    capture.add_argument("--response-dir", type=Path, help="replay response-01.xml/json, response-02.xml/json, etc.")
    parser.add_argument("--captured-at", help="ISO-8601 timestamp with timezone for replay provenance")
    args = parser.parse_args(argv)
    queries = list(args.query)
    if args.query_file:
        try:
            from_file = json.loads(args.query_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            parser.error(f"无法读取 --query-file（{error}）")
        if not isinstance(from_file, list) or not all(isinstance(query, str) for query in from_file):
            parser.error("--query-file 必须是 JSON 字符串数组")
        queries.extend(from_file)
    queries = list(dict.fromkeys(query.strip() for query in queries))
    if not 1 <= len(queries) <= 20 or any(not query or len(query) > 600 or len(query.split()) > 75 for query in queries):
        parser.error("请提供 1 至 20 条查询，每条需为非空且至多 600 字符／75 个空格分隔词")
    if args.captured_at:
        if not args.response_dir or not iso_date(args.captured_at):
            parser.error("--captured-at 仅用于回放，需含 ISO-8601 时区")
        args.captured_at = iso_date(args.captured_at)
    if args.freshness and args.provider != "brave":
        parser.error("--freshness 仅适用于 Brave")
    token = os.environ.get("BRAVE_SEARCH_API_KEY", "").strip() if args.provider == "brave" and not args.response_dir else ""
    if args.provider == "brave" and not args.response_dir and not token:
        parser.error("使用 Brave 在线搜索前，请设置 BRAVE_SEARCH_API_KEY")
    return args, queries, token


def main(argv=None):
    args, queries, token = arguments(argv)
    result = run_search(args, queries, token)
    encoded = (json.dumps(result, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        if args.out == "-":
            sys.stdout.buffer.write(encoded)
        else:
            write_atomic(Path(args.out), encoded)
    except OSError as error:
        print(f"无法写入结果（{error}）", file=sys.stderr)
        return 2
    if result["errors"]:
        print(f"搜索状态 {result['status']}，{len(result['errors'])} 条查询失败，详情见 JSON 的 errors。", file=sys.stderr)
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
