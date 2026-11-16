from __future__ import annotations

import zlib

import httpx

from .models import SearchResult


class SearchError(Exception):
    """搜索调用失败。"""


class BochaSearchClient:
    def __init__(self, api_key: str | None,
                 base_url: str = "https://api.bocha.cn/v1/web-search",
                 timeout: float = 30.0):
        self.api_key = api_key
        self.base_url = base_url
        self.timeout = timeout

    def search(self, query: str, count: int = 10) -> list[SearchResult]:
        if not self.api_key:
            raise SearchError("未配置 BOCHA_API_KEY")
        try:
            resp = httpx.post(
                self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "summary": True, "count": count},
                timeout=self.timeout,
            )
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise SearchError(f"Bocha 搜索失败: {e}") from e
        items = self._extract_items(data)
        return [self._to_result(query, item) for item in items
                if isinstance(item, dict)]

    @staticmethod
    def _extract_items(data: dict | list) -> list[dict]:
        # 兼容多种响应形状：data.webPages.value / webPages.value / 顶层 list
        try:
            return data["data"]["webPages"]["value"] or []
        except (KeyError, TypeError):
            pass
        try:
            return data["webPages"]["value"] or []
        except (KeyError, TypeError):
            pass
        if isinstance(data, list):
            return data
        return []

    @staticmethod
    def _to_result(query: str, item: dict) -> SearchResult:
        return SearchResult(
            query=query,
            title=item.get("title") or item.get("name") or "",
            url=item.get("url") or "",
            snippet=item.get("summary") or item.get("snippet") or "",
            date=(item.get("date") or "").strip(),
        )


class MockSearchClient:
    def search(self, query: str, count: int = 10) -> list[SearchResult]:
        seed = zlib.crc32(query.encode("utf-8"))
        return [
            SearchResult(
                query=query,
                title=f"「{query}」相关资料 {i + 1}",
                url=f"https://example.com/r/{seed}-{i + 1}",
                snippet=f"关于「{query}」的第 {i + 1} 条摘要内容。",
                date="2026-09-01",
            )
            for i in range(3)
        ]
