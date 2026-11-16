# -*- coding: utf-8 -*-
"""keyword —— 生成搜索关键词的 agent。

把研究主题拆解成 3-5 个可检索的具体关键词（DeepResearch 流程的第一步「规划」）。
"""
from __future__ import annotations

import logging

from .. import config
from ..models import KeywordOutput
from .base import BaseAgent

logger = logging.getLogger(__name__)


class KeywordAgent(BaseAgent):
    """生成搜索关键词的 agent。"""

    agent_name = "KeywordAgent"
    template_name = "keyword_agent.jinja2"

    async def generate_keywords(self, topic: str) -> list[str]:
        """根据主题生成 3-5 个可检索关键词。"""
        logger.info("为主题生成关键词: %s", topic)
        out = await self.call_json(
            {"topic": topic, "today": config.today_str()},
            topic,
            KeywordOutput,
        )
        logger.info("生成关键词 %d 个: %s", len(out.keywords), out.keywords)
        return out.keywords


if __name__ == "__main__":
    # 测试 demo：真实调用 LLM 生成关键词（需要 .env 里的 DeepSeek key 与网络）
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    async def _demo() -> None:
        agent = KeywordAgent()
        keywords = await agent.generate_keywords("2026 年主流 Agent 框架对比")
        print("生成关键词:", keywords)

    asyncio.run(_demo())
