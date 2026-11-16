# -*- coding: utf-8 -*-
"""report —— 生成报告的 agent。

两次调用：
1. 输出报告**元信息**（ReportOutline：title/summary/key_conclusions/open_questions）；
   正文分节（sections）由草稿段落直接映射（heading=keyword、body=text），不走 LLM 重新组织；
2. 把组装好的结构化报告（ReportContent）渲染成完整自包含的 HTML 文档
   （含来源列表、置信度说明、遗留问题，内嵌 CSS）。

第二次调用输出的是 HTML 原文（不包 JSON），避免 HTML 在 JSON 里转义出错。
"""
from __future__ import annotations

import json
import logging

from .. import config
from ..models import ConfidenceNote, DraftBlock, ReportContent, ReportOutline, Section, Source
from .base import BaseAgent

logger = logging.getLogger(__name__)


class ReportAgent(BaseAgent):
    """生成结构化报告 + HTML 报告的 agent。"""

    agent_name = "ReportAgent"
    template_name = "report_agent.jinja2"

    async def generate(
        self,
        topic: str,
        draft: list[DraftBlock],
        sources: list[Source],
        confidence: ConfidenceNote,
    ) -> tuple[ReportContent, str]:
        """生成结构化报告与其 HTML 版，返回 (report, report_html)。"""
        materials = {
            "draft": [{"keyword": b.keyword, "text": b.text} for b in draft],
            "sources": [s.model_dump() for s in sources],
            "confidence": confidence.model_dump(),
        }
        user_input = json.dumps(materials, ensure_ascii=False)

        # 第一次调用：报告元信息（正文段落直接来自草稿，不让 LLM 重写）
        logger.info("生成报告元信息: draft 段落=%d sources=%d", len(draft), len(sources))
        outline = await self.call_json(
            {"topic": topic, "today": config.today_str()},
            user_input,
            ReportOutline,
        )
        logger.info(
            "报告元信息完成: 「%s」 关键结论=%d 遗留问题=%d",
            outline.title,
            len(outline.key_conclusions),
            len(outline.open_questions),
        )

        # 正文分节 = 草稿段落直接映射
        report = ReportContent(
            title=outline.title,
            summary=outline.summary,
            sections=[Section(heading=b.keyword, body=b.text) for b in draft],
            key_conclusions=outline.key_conclusions,
            open_questions=outline.open_questions,
        )
        logger.info("结构化报告完成: 分节=%d", len(report.sections))

        # 第二次调用：HTML 报告（final_output 即 HTML 原文）
        report_json = report.model_dump_json(ensure_ascii=False)
        logger.info("渲染 HTML 报告...")
        html = await self._run(
            {"topic": topic, "today": config.today_str()},
            report_json,
            template_name="report_html_agent.jinja2",
        )
        logger.info("HTML 报告完成: %d 字符", len(html))
        return report, html


if __name__ == "__main__":
    # 测试 demo：用模拟素材真实调用 LLM 生成结构化报告 + HTML（需要 .env 里的 DeepSeek key）
    import asyncio
    import logging

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    from ..models import ConfidenceNote, DraftBlock

    _MOCK_DRAFT = [
        DraftBlock(round=1, keyword="Agent 框架", text="OpenAI Agents SDK 轻量、官方维护，适合快速搭建多 agent 应用。"),
        DraftBlock(round=1, keyword="LangGraph", text="LangGraph 把 agent 建模为有向图，适合复杂可控的多步工作流。"),
    ]

    async def _demo() -> None:
        agent = ReportAgent()
        report, html = await agent.generate(
            "2026 年主流 Agent 框架对比",
            _MOCK_DRAFT,
            [],
            ConfidenceNote(overall="low", info_cutoff="2026-08-17"),
        )
        print("标题:", report.title)
        print("分节:", len(report.sections), "| 各节标题:", [s.heading for s in report.sections])
        print("关键结论:", len(report.key_conclusions))
        print("HTML 长度:", len(html), "| 是否含 <html:", "<html" in html.lower())

    asyncio.run(_demo())
