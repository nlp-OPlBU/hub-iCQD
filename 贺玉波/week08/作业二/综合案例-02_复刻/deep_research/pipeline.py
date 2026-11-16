from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .agents import AgentError, AgentRunner
from .llm import LLMError
from .models import (
    Citation, Confidence, ExtractedNote, GapDecision, Report, ReportSection,
    ResearchPlan, RoundRecord, SearchResult, SubQuestion, UnsourcedClaim,
)
from .search import SearchError


@dataclass
class ResearchOutput:
    report: Report
    process: list[RoundRecord]
    total_queries: int


class Pipeline:
    def __init__(self, llm, search, max_rounds: int = 2,
                 max_total_queries: int = 30,
                 on_event: Callable[[str], None] | None = None):
        self.agents = AgentRunner(llm)
        self.search = search
        self.max_rounds = max_rounds
        self.max_total_queries = max_total_queries
        self.on_event = on_event or (lambda msg: None)

    def _emit(self, msg: str) -> None:
        self.on_event(msg)

    def run(self, topic: str) -> ResearchOutput:
        # 过程记录/要点池在 try 外初始化：LLM 传输层失败时需保留已生成的记录（规格 §4）
        process: list[RoundRecord] = []
        all_notes: list[ExtractedNote] = []
        all_dates: list[str] = []  # 搜索结果中的来源日期（置信度的信息截止时间）
        total_queries = 0
        try:
            # ── 规划 ──
            self._emit(f"[规划] 拆解主题：{topic}")
            try:
                plan = self.agents.plan(topic)
            except AgentError as e:
                self._emit(f"[规划] 失败，降级为主题本身作为唯一子问题：{e}")
                plan = ResearchPlan(
                    sub_questions=[SubQuestion(id="sq1", question=topic)],
                    search_queries={"sq1": [topic]},
                    suggested_rounds=1,
                )

            # ── 逐子问题：检索 → 阅读 → 补检判断 ──
            for sq in plan.sub_questions:
                sq.status = "in_progress"
                notes: list[ExtractedNote] = []
                queries = list(plan.search_queries.get(sq.id, [sq.question]))
                round_no = 0
                while True:
                    round_no += 1
                    queries = [q for q in queries if q.strip()]
                    results: list[SearchResult] = []
                    for q in queries:
                        if total_queries >= self.max_total_queries:
                            self._emit(f"[检索] 达到总检索预算 {self.max_total_queries}，停止检索")
                            break
                        total_queries += 1
                        self._emit(f"[检索] 第{round_no}轮：「{q}」")
                        try:
                            results.extend(self.search.search(q))
                        except SearchError as e:
                            self._emit(f"[检索] 失败：{e}")
                    for r in results:
                        if r.date and r.date not in all_dates:
                            all_dates.append(r.date)

                    if not results:
                        self._emit(f"[阅读] 子问题 {sq.id} 本轮无有效结果，低置信度结束")
                        process.append(RoundRecord(
                            round_no=round_no, sub_question_id=sq.id, queries=queries,
                            pages_read=0, new_findings=0,
                            gap_decision=GapDecision(need_more=False, reason="检索无结果")))
                        sq.status = "low_confidence"
                        break

                    try:
                        new_notes = self.agents.read(sq, results)
                    except AgentError as e:
                        self._emit(f"[阅读] 子问题 {sq.id} 抽取失败，低置信度跳过：{e}")
                        process.append(RoundRecord(
                            round_no=round_no, sub_question_id=sq.id, queries=queries,
                            pages_read=len(results), new_findings=0,
                            gap_decision=GapDecision(need_more=False, reason="抽取失败")))
                        sq.status = "low_confidence"
                        break
                    except LLMError:
                        # 传输层失败：记录本轮后向外传播，由外层统一终止
                        process.append(RoundRecord(
                            round_no=round_no, sub_question_id=sq.id, queries=queries,
                            pages_read=len(results), new_findings=0,
                            gap_decision=GapDecision(need_more=False, reason="抽取中断")))
                        raise
                    notes.extend(new_notes)
                    self._emit(f"[阅读] 子问题 {sq.id} 抽取 {len(new_notes)} 条要点")

                    try:
                        decision = self.agents.refine(sq, notes)
                    except AgentError as e:
                        self._emit(f"[补检] 判断失败，按信息充足处理：{e}")
                        decision = GapDecision(need_more=False, reason="判断失败降级")
                    except LLMError:
                        process.append(RoundRecord(
                            round_no=round_no, sub_question_id=sq.id, queries=queries,
                            pages_read=len(results), new_findings=len(new_notes),
                            gap_decision=GapDecision(need_more=False, reason="补检判断中断")))
                        raise
                    process.append(RoundRecord(
                        round_no=round_no, sub_question_id=sq.id, queries=queries,
                        pages_read=len(results), new_findings=len(new_notes),
                        gap_decision=decision))

                    if not decision.need_more:
                        self._emit(f"[补检] 子问题 {sq.id} 信息充足，完成")
                        sq.status = "completed"
                        break
                    if round_no > self.max_rounds:
                        self._emit(f"[补检] 子问题 {sq.id} 达到轮数上限，完成")
                        sq.status = "completed"
                        break
                    if total_queries >= self.max_total_queries:
                        self._emit(f"[补检] 达到总检索预算，完成")
                        sq.status = "completed"
                        break
                    queries = list(decision.extra_queries or [sq.question])

                all_notes.extend(notes)

            # ── 综合 ──
            self._emit("[综合] 生成报告")
            try:
                report = self.agents.synthesize(topic, all_notes)
            except AgentError as e:
                self._emit(f"[综合] 失败，降级为基础报告：{e}")
                report = _fallback_report(topic, all_notes)
            report.citations = build_citations(all_notes)
            # 置信度由代码确定性计算（来源数分级 + 最新来源日期），
            # 只保留 LLM 产出的「模型推断」标注
            report.confidence = _compute_confidence(
                source_count=len(report.citations), dates=all_dates,
                unsourced_claims=report.confidence.unsourced_claims)
            return ResearchOutput(report=report, process=process,
                                  total_queries=total_queries)
        except LLMError as e:
            # LLM 传输层失败：终止研究循环，但保留已生成的过程记录与要点（规格 §4）
            self._emit(f"[中断] LLM 调用失败，终止并保留已生成的过程记录：{e}")
            report = _fallback_report(topic, all_notes)
            report.title = f"「{topic}」研究报告（LLM 调用中断，降级生成）"
            report.citations = build_citations(all_notes)
            report.confidence = _compute_confidence(
                source_count=len(report.citations), dates=all_dates,
                unsourced_claims=report.confidence.unsourced_claims)
            return ResearchOutput(report=report, process=process,
                                  total_queries=total_queries)


def build_citations(notes: list[ExtractedNote]) -> list[Citation]:
    seen: dict[str, Citation] = {}
    for n in notes:
        if n.source_url and n.source_url not in seen:
            seen[n.source_url] = Citation(
                id=str(len(seen) + 1),
                title=n.source_title or n.source_url,
                url=n.source_url)
    return list(seen.values())


def _compute_confidence(
    source_count: int,
    dates: list[str],
    unsourced_claims: list[UnsourcedClaim] | None = None,
) -> Confidence:
    """确定性计算置信度（不交给 LLM）。

    - overall：来源数 ≥12 高、≥5 中、其余低
    - info_cutoff_time：来源日期中最新者；无来源日期时取研究执行日
    - unsourced_claims：保留 LLM 产出的「模型推断」标注
    """
    if source_count >= 12:
        overall = "高"
    elif source_count >= 5:
        overall = "中"
    else:
        overall = "低"

    candidates = [d[:10] for d in dates if d]
    info_cutoff = max(candidates) if candidates else datetime.now().strftime("%Y-%m-%d")

    notes = [
        f"正文引用 {source_count} 个来源；总体置信度按来源数量分级（≥12 高、≥5 中、其余低）。",
        f"信息截止时间取检索结果中的最新资料来源日期（{info_cutoff}）；无来源日期时取研究执行日。",
    ]
    if unsourced_claims:
        notes.append("未关联任何来源的结论已在下方标注为「模型推断」。")
    return Confidence(
        overall=overall, info_cutoff_time=info_cutoff, notes="".join(notes),
        unsourced_claims=unsourced_claims or [],
    )


def _fallback_report(topic: str, notes: list[ExtractedNote]) -> Report:
    body = "\n".join(f"- {n.text}（来源：{n.source_title or n.source_url}）" for n in notes) or "无有效要点。"
    return Report(
        title=f"「{topic}」研究报告（降级生成）",
        summary="综合阶段失败，本报告由已抽取要点直接拼装，未经过模型综合。",
        sections=[ReportSection(heading="要点汇总", body=body)],
        conclusions=[],
        open_questions=["综合阶段失败，未能生成结论与遗留问题。"],
    )
