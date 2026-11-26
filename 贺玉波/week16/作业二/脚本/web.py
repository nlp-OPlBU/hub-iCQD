#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Deterministic web access layer used by both ESG skills.

search(query, num)  -> [{title,url,snippet,date,engine}]   (multi-engine, cached)
fetch(url)          -> {url,final_url,status,title,text,error}

Design goals:
  * Same query + same day => same candidate set (no LLM randomness in retrieval).
  * Every item keeps URL + snippet so downstream claims stay traceable.
  * Local cache => reruns are reproducible and cheap.
  * Provider fallback + block detection => one engine's captcha does not kill a run.
  * "imports" provider => allow a human/agent to plug in search results (e.g. when the
    corporate network blocks engines, or when using a licensed search API).

CLI:
  python3 web.py search "ESG 政策" --num 10
  python3 web.py fetch https://example.com/a.html
"""
import argparse
import hashlib
import json
import os
import random
import re
import sys
import time
from urllib.parse import parse_qs, quote, urlparse

import requests
from bs4 import BeautifulSoup

CACHE_DIR = os.environ.get("ESG_CACHE_DIR", os.path.join(os.path.dirname(__file__), "..", ".cache"))
OFFLINE = os.environ.get("ESG_OFFLINE") == "1"
UA_MOBILE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
             "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")
UA_DESKTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
HEADERS = {"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.6",
           "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"}
BLOCK_MARKERS = ["_____tmd_____/punish", "访问异常", "安全验证", "验证码", "captcha", "人机验证"]
DATE_RE = re.compile(r"(20\d{2})\s*[年\-/.]\s*(\d{1,2})\s*[月\-/.]\s*(\d{1,2})")


# ---------------------------------------------------------------- cache helpers
def _path(kind, key):
    os.makedirs(os.path.join(CACHE_DIR, kind), exist_ok=True)
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, kind, h + ".json")


def _cache_get(kind, key, ttl_hours=72):
    p = _path(kind, key)
    if not os.path.exists(p):
        return None
    if ttl_hours is not None and (time.time() - os.path.getmtime(p)) / 3600.0 > ttl_hours:
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _cache_put(kind, key, value):
    with open(_path(kind, key), "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=1)


def _blocked_file():
    return os.path.join(CACHE_DIR, "blocked.json")


COOLDOWN_S = float(os.environ.get("ESG_COOLDOWN_S", "18"))
_rr = [0]  # round-robin cursor for engine rotation


def _is_cooling(engine, seconds=None):
    """Engines recover in ~20-40s, so cooling is short and retries are cheap."""
    seconds = COOLDOWN_S if seconds is None else seconds
    try:
        with open(_blocked_file(), encoding="utf-8") as f:
            st = json.load(f)
    except Exception:
        return False
    return (time.time() - st.get(engine, 0)) < seconds


def _mark_blocked(engine):
    try:
        try:
            with open(_blocked_file(), encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            st = {}
        st[engine] = time.time()
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_blocked_file(), "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def import_results(query, results, num=10):
    """Plug externally obtained results (licensed API / agent) into the cache."""
    _cache_put("imports", f"{query}::{num}", results)
    return len(results)


# ---------------------------------------------------------------- dates
def find_dates(text):
    out = []
    for m in DATE_RE.finditer(text or ""):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            out.append(f"{y:04d}-{mo:02d}-{d:02d}")
    return out


def find_date(text):
    """Publication-like date: the last date in a snippet is usually the source date."""
    ds = find_dates(text)
    return ds[-1] if ds else ""


# ---------------------------------------------------------------- engines
def _get(url, ua=UA_MOBILE, timeout=20):
    return requests.get(url, headers={**HEADERS, "User-Agent": ua}, timeout=timeout)


def _blocked(html):
    low = (html or "").lower()
    return any(m.lower() in low for m in BLOCK_MARKERS)


def _search_quark(query, num):
    r = _get("https://quark.sm.cn/s?q=" + quote(query))
    if _blocked(r.text):
        _mark_blocked("quark")
        return []
    r.encoding = "utf-8"
    soup = BeautifulSoup(r.text, "lxml")
    out, seen = [], set()
    for card in soup.select("div.qk-card"):
        a = card.select_one("a[href^='http']")
        if not a:
            continue
        href = a.get("href", "").strip()
        if not href.startswith("http") or "quark.sm.cn/s?" in href:
            continue
        t = card.select_one(".qk-title")
        title = t.get_text(" ", strip=True) if t else a.get_text(" ", strip=True)
        body = card.get_text(" ", strip=True)
        snippet = re.sub(r"\s+", " ", body.replace(title, "", 1))[:400]
        if not title or href in seen:
            continue
        seen.add(href)
        out.append({"title": title[:200], "url": href, "snippet": snippet,
                    "date": find_date(body), "engine": "quark"})
        if len(out) >= num:
            break
    if not out:
        # zero results is not a block; only a suspiciously short page is
        if len(r.text) < 3000:
            _mark_blocked("quark")
    return out


def _search_360(query, num):
    r = _get("https://www.so.com/s?q=" + quote(query), ua=UA_DESKTOP)
    if _blocked(r.text) or len(r.text) < 20000:
        _mark_blocked("360")
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for h in soup.select("h3"):
        a = h.find("a", href=True)
        if not a:
            continue
        title = h.get_text(" ", strip=True)
        if not title or "其他人还搜" in title:
            continue
        li = h.find_parent("li") or h.find_parent("div")
        body = li.get_text(" ", strip=True) if li else title
        out.append({"title": title[:200], "url": a["href"], "snippet": body[:400],
                    "date": find_date(body), "engine": "360"})
        if len(out) >= num:
            break
    return out


def _search_sogou(query, num):
    r = _get("https://m.sogou.com/web/searchList.jsp?keyword=" + quote(query))
    if _blocked(r.text):
        _mark_blocked("sogou")
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for blk in soup.select("div.reactResult"):
        h3 = blk.select_one("h3")
        if not h3:
            continue
        title = h3.get_text(" ", strip=True)
        a = h3.find("a", href=True) or blk.find("a", href=True)
        href = _sogou_target(a.get("href", "") if a else "")
        if not href:
            continue
        body = blk.get_text(" ", strip=True)
        out.append({"title": title[:200], "url": href, "snippet": re.sub(r"\s+", " ", body)[:400],
                    "date": find_date(body), "engine": "sogou"})
        if len(out) >= num:
            break
    return out


def _sogou_target(href):
    """Sogou mobile wraps results in redirect links; the real URL sits in pcurl=/url=."""
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    if href.startswith("./"):
        href = "https://m.sogou.com/web/" + href[2:]
    parsed = urlparse(href)
    if "sogou.com" in parsed.netloc:
        for key in ("pcurl", "url"):
            val = parse_qs(parsed.query).get(key)
            if val and val[0].startswith("http"):
                return val[0]
        return ""
    return href if href.startswith("http") else ""


def _search_bing(query, num):
    r = _get("https://cn.bing.com/search?q=" + quote(query) + "&mkt=zh-CN", ua=UA_DESKTOP)
    if _blocked(r.text):
        _mark_blocked("bing")
        return []
    soup = BeautifulSoup(r.text, "lxml")
    out = []
    for li in soup.select("li.b_algo"):
        a = li.select_one("h2 a")
        if not a:
            continue
        cap = li.select_one(".b_caption") or li
        body = cap.get_text(" ", strip=True)
        out.append({"title": a.get_text(" ", strip=True)[:200], "url": a.get("href", ""),
                    "snippet": body[:400], "date": find_date(body), "engine": "bing"})
        if len(out) >= num:
            break
    return out


# 360 only returns JS redirect links (so.com/link?m=...) which cannot be resolved,
# so it is kept as an explicit fallback but excluded from the automatic chain.
ENGINES = [("quark", _search_quark), ("sogou", _search_sogou)]


def _tokens(query):
    """Substantive tokens from a Chinese query (drop generic time words / stopwords)."""
    drop = {"新闻", "最新", "进展", "情况", "动态", "报告", "2024年", "2025年", "2026年"}
    out = []
    for t in query.split():
        t = t.strip()
        if len(t) < 2:
            continue
        if re.fullmatch(r"20\d{2}年(\d{1,2}月)?", t) or t in drop:
            continue
        out.append(t)
    return out


def _relevant(query, results):
    """Keep results that actually mention the subject (guards against junk fallbacks)."""
    toks = _tokens(query)
    if not toks:
        return results
    kept = []
    for r in results:
        text = (r.get("title", "") + " " + r.get("snippet", "")).replace(" ", "")
        if any(t.replace(" ", "") in text for t in toks):
            kept.append(r)
    return kept


def search(query, num=10, engine="auto", use_cache=True, ttl_hours=24, attempts=3):
    """Search with provider fallback + short backoff retry when every engine cools."""
    key = f"{engine}:{query}:{num}"
    if use_cache:
        c = _cache_get("search", key, ttl_hours)
        if c:
            return _relevant(query, c)
    imp = _cache_get("imports", f"{query}::{num}", None)
    if imp:
        if use_cache:
            _cache_put("search", key, imp)
        return imp
    if OFFLINE:
        return []
    if engine != "auto":
        fn = dict(ENGINES).get(engine)
        res = fn(query, num) if fn else []
        if res and use_cache:
            _cache_put("search", key, res)
        return res
    res = []
    for attempt in range(attempts):
        # rotate the starting engine so one provider is not hammered
        start = _rr[0] % len(ENGINES)
        _rr[0] += 1
        order = ENGINES[start:] + ENGINES[:start]
        for name, fn in order:
            if _is_cooling(name):
                continue
            try:
                res = _relevant(query, fn(query, num))
            except Exception as e:
                sys.stderr.write(f"[search:{name}] {e}\n")
                res = []
            if res:
                break
            time.sleep(1.5)
        if res:
            break
        if attempt < attempts - 1:
            # all engines cooled or returned nothing: wait out the cooldown
            time.sleep(COOLDOWN_S + random.uniform(3, 10))
    if res and use_cache:
        _cache_put("search", key, res)
    return res


# ---------------------------------------------------------------- page fetch
BLOCK_TAGS = ["script", "style", "noscript", "nav", "footer", "header", "aside", "form",
              "svg", "iframe", "button", "select"]


def _extract_text(html):
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(BLOCK_TAGS):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    node = None
    for sel in ["article", "main", ".article-content", ".content", "#content",
                ".post-content", ".detail", ".text"]:
        cand = soup.select_one(sel)
        if cand and len(cand.get_text(strip=True)) > 200:
            node = cand
            break
    if node is None:
        node = soup.body or soup
    text = node.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, re.sub(r"[ \t\u3000]+", " ", text)


def fetch(url, timeout=15, use_cache=True, ttl_hours=168, max_chars=15000):
    if use_cache:
        c = _cache_get("pages", url, ttl_hours)
        if c is not None:
            return c
    rec = {"url": url, "final_url": url, "status": 0, "title": "", "text": "", "error": ""}
    if OFFLINE:
        rec["error"] = "offline_no_cache"
        return rec
    try:
        r = requests.get(url, headers={**HEADERS, "User-Agent": UA_DESKTOP},
                         timeout=timeout, allow_redirects=True)
        rec["status"], rec["final_url"] = r.status_code, r.url
        if r.status_code >= 400 or _blocked(r.text[:5000]):
            rec["error"] = f"HTTP {r.status_code}"
        else:
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            title, text = _extract_text(r.text)
            rec["title"], rec["text"] = title[:200], text[:max_chars]
            if len(text) < 80:
                rec["error"] = "empty_or_js_page"
    except Exception as e:
        rec["error"] = type(e).__name__ + ": " + str(e)[:100]
    if use_cache:
        _cache_put("pages", url, rec)
    return rec


def fetch_many(urls, limit=None, pause=(0.5, 1.0)):
    out = []
    for i, u in enumerate(urls):
        if limit is not None and i >= limit:
            break
        out.append(fetch(u))
        time.sleep(random.uniform(*pause))
    return out


def domain_of(url):
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser(description="Deterministic search/fetch helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--num", type=int, default=10)
    s.add_argument("--engine", default="auto")
    s.add_argument("--out", default="")
    f = sub.add_parser("fetch")
    f.add_argument("url")
    f.add_argument("--chars", type=int, default=4000)
    f.add_argument("--out", default="")
    args = ap.parse_args()

    if args.cmd == "search":
        res = search(args.query, args.num, args.engine)
        payload = json.dumps(res, ensure_ascii=False, indent=2)
    else:
        rec = fetch(args.url)
        rec["text"] = rec["text"][:args.chars]
        payload = json.dumps(rec, ensure_ascii=False, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(payload)
    else:
        print(payload)


if __name__ == "__main__":
    main()
