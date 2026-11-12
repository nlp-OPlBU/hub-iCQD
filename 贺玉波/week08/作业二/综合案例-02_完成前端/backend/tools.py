# -*- coding: utf-8 -*-
"""研究用工具：Bocha 网页搜索。

web_search 是普通 async 函数（不再注册为 function_tool）——DeepResearch
编排器直接调用它完成检索，返回解析后的结果列表；子 agent 本身不挂工具。
"""
from __future__ import annotations

import httpx

from . import config


async def web_search(query: str) -> list[dict]:
    """调用 Bocha 网页搜索，返回解析后的结果列表。

    每项含 title / url / snippet / site_name / date；查询失败时抛异常，
    由调用方（DeepResearch）统一兜底为空结果。
    """
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(
            "https://api.bocha.cn/v1/web-search",
            headers={
                "Authorization": f"Bearer {config.BOCHA_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"query": query, "summary": True, "count": config.BOCHA_SEARCH_COUNT},
        )
        resp.raise_for_status()
        data = resp.json()

    pages = data.get("data", {}).get("webPages", {}).get("value", [])
    results = []
    for p in pages:
        snippet = (p.get("snippet") or p.get("summary") or "")[:500]
        results.append(
            {
                "title": p.get("name"),
                "url": p.get("url"),
                "snippet": snippet,
                "site_name": p.get("siteName"),
                "date": p.get("datePublished") or p.get("dateLastCrawled"),
            }
        )
    return results


if __name__ == "__main__":
    # 测试 demo：真实调用 Bocha 搜索（需要 .env 里的 BOCHA_API_KEY 与网络）
    import asyncio

    async def _demo() -> None:
        results = await web_search("天空为什么是蓝色的")
        print(f"检索到 {len(results)} 条结果，前 3 条：")
        for r in results[:3]:
            print(" -", r.get("title"), "|", r.get("url"), "|", r.get("date"))

    asyncio.run(_demo())
