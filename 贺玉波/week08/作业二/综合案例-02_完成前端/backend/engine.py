# -*- coding: utf-8 -*-
"""engine —— 研究引擎：组合 agent/ 里各角色 agent 的完整研究流程（编排器）。

engine 不是 agent：它不参与任何一次 LLM 调用，而是做确定性的控制流——
调用角色 agent（KeywordAgent / SummaryAgent / JudgeAgent / ReportAgent）、
直接调搜索工具（tools.web_search）、收集来源、计算置信度、驱动循环收敛。

流程：
1. 规划：KeywordAgent 生成初始关键词；
2. 循环检索：对每个关键词 web_search → SummaryAgent 把结果综合成一段正文文字 →
   直接累积进报告草稿（draft）并顺带收集来源 → 用当前累积的草稿文字让 JudgeAgent
   判断是否补检；不足则用其新关键词进入下一轮，直到足够或到达 max_rounds 上限；
3. 综合：确定性计算置信度、ReportAgent 生成结构化报告（元信息 + 草稿映射正文）+ HTML。

中间结果（process.steps / draft / sources）在每轮结束后通过 on_progress 回调交给
调用方（research.py）落盘，实现「中间结果也可以保存下来」。
"""
from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable

from . import config, tools
from .agent.judge import JudgeAgent
from .agent.keyword import KeywordAgent
from .agent.report import ReportAgent
from .agent.summary import SummaryAgent
from .models import (
    ConfidenceNote,
    DeepResearchResult,
    DraftBlock,
    ProcessStep,
    ResearchProcess,
    Source,
)

logger = logging.getLogger(__name__)


class DeepResearch:
    """组合 agent/ 各角色 agent 的研究引擎（编排器）。"""

    def __init__(self, topic: str):
        self.topic = topic
        self.keyword_agent = KeywordAgent()
        self.summary_agent = SummaryAgent()
        self.judge_agent = JudgeAgent()
        self.report_agent = ReportAgent()

    async def run(
        self,
        max_rounds: int | None = None,
        on_progress: Callable[[dict], Awaitable[None] | None] | None = None,
    ) -> DeepResearchResult:
        """执行完整研究，返回 DeepResearchResult。

        max_rounds  检索/判断轮数上限（默认取配置 RESEARCH_MAX_ROUNDS，3 轮）。
        on_progress 每完成一轮（含规划）回调一次，参数为当前中间结果快照
                    {"process": {...}, "draft": [{round, keyword, text}],
                     "sources": [{url, title, ...}]}。
                    回调可为同步或异步函数，返回值（若有）会被 await。
        """
        topic = self.topic
        max_rounds = max_rounds or config.MAX_ROUNDS

        process = ResearchProcess()
        draft: list[DraftBlock] = []  # 报告正文草稿（每检索一个关键词累积一段）
        sources: list[Source] = []  # 来源列表（按 URL 去重）
        seen_urls: set[str] = set()
        all_dates: list[str] = []

        # ---- 1. 规划：生成初始关键词 ----
        initial_keywords = await self.keyword_agent.generate_keywords(topic)
        if not initial_keywords:
            initial_keywords = [topic]
        process.plan = list(initial_keywords)
        process.steps.append(
            ProcessStep(type="plan", round=0, detail={"keywords": initial_keywords})
        )
        logger.info("规划完成，初始关键词 %d 个: %s", len(initial_keywords), initial_keywords)
        await self._progress(on_progress, process, draft, sources)

        # ---- 2. 检索 → 总结 → 累积正文 → 判断循环 ----
        searched: list[str] = []  # 实际检索过的全部关键词（去重）
        todo_keywords: list[str] = list(initial_keywords)
        round_no = 0

        while round_no < max_rounds:
            round_no += 1
            process.iterations = round_no

            for kw in todo_keywords:
                if kw in searched:
                    continue
                searched.append(kw)
                process.search_queries.append(kw)

                try:
                    results = await tools.web_search(kw)
                except Exception:  # noqa: BLE001 - 单次检索失败不中断流程
                    results = []

                _collect_sources(results, seen_urls, sources, process, all_dates)
                process.steps.append(
                    ProcessStep(
                        type="search",
                        round=round_no,
                        detail={"keyword": kw, "results": len(results)},
                    )
                )

                # 总结成一段正文文字，直接累积进报告草稿
                text = await self.summary_agent.summarize(topic, kw, results)
                text = (text or "").strip()
                draft.append(DraftBlock(round=round_no, keyword=kw, text=text))
                logger.info(
                    "第 %d 轮检索「%s」→ 结果 %d 条 / 累积正文 %d 段",
                    round_no,
                    kw,
                    len(results),
                    len(draft),
                )
                process.steps.append(
                    ProcessStep(
                        type="summarize",
                        round=round_no,
                        detail={"keyword": kw, "chars": len(text)},
                    )
                )

            # 判断补检（基于当前累积的草稿文字）
            draft_text = _join_draft(draft)
            decision = await self.judge_agent.judge(topic, draft_text, sources, searched)
            logger.info(
                "第 %d 轮判断: sufficient=%s new_keywords=%s",
                round_no,
                decision.sufficient,
                decision.new_keywords,
            )
            process.steps.append(
                ProcessStep(
                    type="judge",
                    round=round_no,
                    detail={
                        "sufficient": decision.sufficient,
                        "reason": decision.reason,
                        "new_keywords": decision.new_keywords,
                    },
                )
            )

            # 每轮结束保存中间结果（status 仍为 running）
            await self._progress(on_progress, process, draft, sources)

            if decision.sufficient or round_no >= max_rounds:
                break
            # 生成下一轮要检索的新关键词（跳过已检索过的）
            todo_keywords = [
                k for k in (decision.new_keywords or []) if k not in searched
            ]
            if not todo_keywords:
                break

        # ---- 3. 综合：置信度 / 报告 ----
        confidence = _compute_confidence(sources, draft, all_dates)
        report, report_html = await self.report_agent.generate(
            topic, draft, sources, confidence
        )
        logger.info(
            "研究完成: 共 %d 轮 / %d 个关键词 / 正文 %d 段 / 来源 %d 个 / 置信度 %s",
            process.iterations,
            len(process.search_queries),
            len(draft),
            len(sources),
            confidence.overall,
        )

        return DeepResearchResult(
            report=report,
            report_html=report_html,
            sources=sources,
            draft=draft,
            process=process,
            confidence=confidence,
        )

    @staticmethod
    async def _progress(
        on_progress: Callable[[dict], Awaitable[None] | None] | None,
        process: ResearchProcess,
        draft: list[DraftBlock],
        sources: list[Source],
    ) -> None:
        """若注册了 on_progress 回调，就推送当前中间结果快照。

        兼容同步与异步回调（同 agents SDK 处理 function_tool 的方式）：
        调用后若返回值是 awaitable 才 await，否则（同步回调）直接略过。
        """
        if on_progress is None:
            return
        result = on_progress(
            {
                "process": process.model_dump(),
                "draft": [b.model_dump() for b in draft],
                "sources": [s.model_dump() for s in sources],
            }
        )
        if inspect.isawaitable(result):
            await result


def _collect_sources(
    results: list[dict],
    seen_urls: set[str],
    sources: list[Source],
    process: ResearchProcess,
    all_dates: list[str],
) -> None:
    """把一次搜索的结果收进来源列表（按 URL 去重），并记录 URL / 日期。"""
    for r in results:
        url = (r.get("url") or "").strip()
        if url and url not in seen_urls:
            seen_urls.add(url)
            process.reviewed_urls.append(url)
            sources.append(
                Source(
                    url=url,
                    title=r.get("title") or "",
                    site_name=r.get("site_name") or "",
                    snippet=r.get("snippet") or "",
                    accessed_at=config.today_str(),
                )
            )
        date = r.get("date")
        if date and date not in all_dates:
            all_dates.append(date)


def _join_draft(draft: list[DraftBlock]) -> str:
    """把草稿段落拼成一段连续文字（供 judge 判断用），标注各自的关键词。"""
    return "\n\n".join(
        f"【关键词：{b.keyword}】\n{b.text}" for b in draft if (b.text or "").strip()
    )


def _compute_confidence(
    sources: list[Source],
    draft: list[DraftBlock],
    all_dates: list[str],
) -> ConfidenceNote:
    """确定性计算置信度：按来源数量分级，信息截止取最新资料来源日期（缺省今天）。"""
    source_count = len(sources)
    draft_count = len(draft)

    if source_count >= 12:
        overall = "high"
    elif source_count >= 5:
        overall = "medium"
    else:
        overall = "low"

    info_cutoff = _latest_date(all_dates) or config.today_str()

    notes = [
        f"正文由 {draft_count} 段（每检索关键词一段）组成，引用 {source_count} 个来源。",
        f"信息截止时间取检索结果中的最新资料来源日期（{info_cutoff}）。",
        "未关联任何来源的结论为模型推断，报告中已单独标注。",
    ]
    return ConfidenceNote(overall=overall, info_cutoff=info_cutoff, notes=notes)


def _latest_date(dates: list[str]) -> str:
    """取日期列表中的最新日期（支持 YYYY-MM-DD 及常见日期字符串前缀）。"""
    candidates = [d[:10] for d in dates if d]
    return max(candidates) if candidates else ""


if __name__ == "__main__":
    # 测试 demo：完整跑一遍研究引擎流水线，实时打印中间进度，
    # 并把最终结果保存到文件（需要 DeepSeek key + Bocha key + 网络）
    import asyncio
    import logging
    from datetime import datetime

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")

    async def _demo() -> None:
        engine = DeepResearch("2026 年主流 Agent 框架对比")

        def on_progress(snapshot: dict) -> None:
            process = snapshot["process"]
            last = process["steps"][-1] if process["steps"] else {}
            print(
                f"  [进度] 轮次={process['iterations']} "
                f"steps={len(process['steps'])} "
                f"正文段数={len(snapshot['draft'])} "
                f"来源={len(snapshot['sources'])} "
                f"最近一步={last.get('type')}"
            )

        result = await engine.run(on_progress=on_progress)
        print("标题:", result.report.title)
        print("分节:", len(result.report.sections), "| 关键结论:", len(result.report.key_conclusions))
        print("来源:", len(result.sources), "| 置信度:", result.confidence.overall)
        print("HTML 长度:", len(result.report_html))

        # 保存最终结果到文件：完整 JSON + 可直接打开的 HTML
        config.ensure_data_dir()
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        # 存到 backend/data/（DATA_DIR 的父目录），避开 research/ 落盘目录——
        # storage.list_all 只扫 research/*.json 并按 ResearchRecord 解析，不能混放
        out_dir = config.DATA_DIR.parent
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / f"engine_demo_{stamp}.json"
        html_path = out_dir / f"engine_demo_{stamp}.html"
        json_path.write_text(result.model_dump_json(indent=2, ensure_ascii=False), encoding="utf-8")
        html_path.write_text(result.report_html, encoding="utf-8")
        print("最终结果已保存到:")
        print("  JSON:", json_path)
        print("  HTML:", html_path)

    asyncio.run(_demo())
