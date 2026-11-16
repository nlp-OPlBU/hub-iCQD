# -*- coding: utf-8 -*-
"""summary —— 对搜索结果进行总结的 agent。

给定一个关键词及其搜索结果（标题/URL/摘要），把结果综合成一段**可直接进入报告正文**
的文字。这段文字由 DeepResearch 直接累积进报告草稿，不再拆成带来源的 Fact。

输出本质上就是一个字符串、没有任何结构化字段，因此**不走 JSON 解析**（call_json），
直接用基类的 `_run` 拿模型原始输出即可（模板要求直接输出正文文字，不用 ```json 包裹）。
"""
from __future__ import annotations

import json
import logging
import re

from .. import config
from .base import BaseAgent

logger = logging.getLogger(__name__)


class SummaryAgent(BaseAgent):
    """总结搜索结果的 agent。"""

    agent_name = "SummaryAgent"
    template_name = "summary_agent.jinja2"

    async def summarize(
        self,
        topic: str,
        keyword: str,
        results: list[dict],
    ) -> str:
        """把一个关键词的搜索结果综合成一段报告正文文字（来源 URL 由引擎另收）。"""
        logger.info("总结关键词「%s」的 %d 条搜索结果", keyword, len(results))
        user_input = json.dumps(results, ensure_ascii=False)
        text = await self._run(
            {"topic": topic, "keyword": keyword, "today": config.today_str()},
            user_input,
        )
        text = (text or "").strip()
        # 兜底：模型偶尔会顺手把正文包进 ``` ... ``` 代码块，这里去掉（对齐 parse_json 的 fence 兜底思路）
        if text.startswith("```"):
            text = re.sub(r"^```[a-zA-Z]*\s*", "", text).removesuffix("```").strip()
        logger.info("关键词「%s」→ 总结正文 %d 字", keyword, len(text))
        return text


if __name__ == "__main__":
    # 测试 demo：用一条模拟结果真实调用 LLM 总结（需要 .env 里的 DeepSeek key）
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    _MOCK_RESULTS = [
        {
            "title": "OpenAI Agents SDK 官方文档",
            "url": "https://openai.github.io/openai-agents-python/",
            "snippet": "轻量级、可逐步构建的 agent 框架，支持工具调用与多 agent 协作。",
            "site_name": "OpenAI",
            "date": "2026-08-01",
        },
        {
            "title": "LangGraph 官方文档",
            "url": "https://langchain-ai.github.io/langgraph/",
            "snippet": "把 agent 建模为有状态的图，适合复杂可控的多步工作流。",
            "site_name": "LangChain",
            "date": "2026-07-20",
        },
    ]

    async def _demo() -> None:
        agent = SummaryAgent()
        text = await agent.summarize("2026 年主流 Agent 框架对比", "Agent 框架", _MOCK_RESULTS)
        print(f"总结正文 {len(text)} 字：\n{text}")

    asyncio.run(_demo())
