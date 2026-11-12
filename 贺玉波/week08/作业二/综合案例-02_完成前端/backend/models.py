# -*- coding: utf-8 -*-
"""研究请求 / 报告 / 来源 / 过程 / 置信度 / 落盘记录的 Pydantic 模型。

对应产品规格的四类产物，外加多 agent 流水线的中间结果：
- report / report_html  结构化报告（摘要、分节正文、关键结论、遗留问题）+ HTML 版
- sources     来源列表（每条结论关联 URL / 标题，可追溯）
- process     研究过程记录（检索关键词、读过页面、迭代轮数、逐步中间结果）
- draft       报告正文草稿（每个关键词检索内容总结出的文字段落，直接累积成正文）
- confidence  置信度说明（可靠程度、信息截止时间；无来源结论标注"模型推断"）
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Status(str, Enum):
    """研究任务状态。"""

    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"


class ResearchRequest(BaseModel):
    """发起一次研究的请求体。"""

    topic: str = Field(..., min_length=1, max_length=500, description="研究主题")


class SourceRef(BaseModel):
    """单条事实/结论关联的一个来源引用。"""

    url: str
    title: str = ""


class JudgeDecision(BaseModel):
    """判断 agent 的决策（中间产物）。"""

    sufficient: bool = False  # 是否已足够，无需再补检
    reason: str = ""
    new_keywords: list[str] = []  # 不足时需要补充检索的新关键词


class KeywordOutput(BaseModel):
    """关键词 agent 的输出结构（由提示词约束，DeepSeek 不支持 output_type）。"""

    keywords: list[str] = Field(..., description="可检索关键词")


class DraftBlock(BaseModel):
    """一个关键词检索内容总结出的报告正文段落（中间产物，直接累积成报告正文）。

    由 DeepResearch 每检索一个关键词追加一条；轮次 / 关键词用于过程记录追溯。
    """

    round: int = 0  # 所属检索轮次
    keyword: str  # 该段对应的检索关键词
    text: str  # 总结出的正文文字


class ProcessStep(BaseModel):
    """研究过程中一步的中间结果记录。"""

    type: str  # plan / search / summarize / judge
    round: int = 0
    detail: dict[str, Any] = {}


class Conclusion(BaseModel):
    """单条结论。"""

    text: str
    sources: list[SourceRef] = []
    # 无任何来源支撑、属于"模型推断"的结论必须为 True
    is_model_inference: bool = False


class Section(BaseModel):
    """报告的一个分节。"""

    heading: str
    body: str
    conclusions: list[Conclusion] = []


class ConfidenceNote(BaseModel):
    """置信度说明。"""

    overall: str = "medium"  # high / medium / low
    info_cutoff: str = ""  # 信息截止时间，如 "2026-08-17"
    notes: list[str] = []


class ReportContent(BaseModel):
    """结构化报告正文（ReportContent = ReportOutline 元信息 + draft 段落映射的 sections）。"""

    title: str
    summary: str
    sections: list[Section]
    key_conclusions: list[Conclusion]
    open_questions: list[str] = []


class ReportOutline(BaseModel):
    """ReportAgent 第一段调用的输出：报告的元信息。

    正文分节（sections）由 draft 段落直接映射（heading=keyword, body=text），
    不走 LLM 重新组织；LLM 只补充标题、摘要、关键结论与遗留问题。
    """

    title: str
    summary: str
    key_conclusions: list[Conclusion] = []
    open_questions: list[str] = []


class Source(BaseModel):
    """来源列表条目（落盘用，含抓取信息）。"""

    url: str
    title: str = ""
    site_name: str = ""
    snippet: str = ""
    accessed_at: str = ""


class ResearchProcess(BaseModel):
    """研究过程记录（DeepResearch 逐步累积，含中间结果）。"""

    plan: list[str] = []  # 初始关键词（规划）
    search_queries: list[str] = []  # 全部检索过的关键词
    reviewed_urls: list[str] = []  # 搜索结果里实际使用的来源 URL
    iterations: int = 0  # 检索/判断轮数
    steps: list[ProcessStep] = []  # 每步中间结果（plan/search/summarize/judge）


class ResearchRecord(BaseModel):
    """最终落盘 / 返回的完整对象。"""

    research_id: str
    topic: str
    status: Status
    created_at: str
    updated_at: str
    error: str | None = None
    report: ReportContent | None = None
    report_html: str = ""  # ReportAgent 产出的完整 HTML 报告
    sources: list[Source] = []
    draft: list[DraftBlock] = []  # 报告正文草稿（中间结果，逐步累积）
    process: ResearchProcess | None = None
    confidence: ConfidenceNote | None = None


class DeepResearchResult(BaseModel):
    """DeepResearch.run() 返回的完整结果。"""

    report: ReportContent
    report_html: str
    sources: list[Source]
    draft: list[DraftBlock]
    process: ResearchProcess
    confidence: ConfidenceNote


if __name__ == "__main__":
    # 测试 demo：构造一条完整研究记录并验证各模型的序列化/反序列化（纯本地）
    req = ResearchRequest(topic="竞品分析：2026 年主流 Agent 框架")
    print("ResearchRequest:", req.model_dump())

    block = DraftBlock(
        round=1,
        keyword="OpenAI Agents SDK",
        text="OpenAI Agents SDK 是轻量级 agent 框架，官方维护，适合快速搭建多 agent 应用。",
    )
    print("DraftBlock:", block.model_dump())

    conf = ConfidenceNote(
        overall="medium",
        info_cutoff="2026-08-17",
        notes=["信息来源有限。"],
    )
    report = ReportContent(
        title="2026 年主流 Agent 框架对比",
        summary="对 OpenAI Agents SDK 与 LangGraph 等的对比分析。",
        sections=[
            Section(
                heading="背景",
                body="介绍。",
                conclusions=[Conclusion(text="均为主流框架（模型推断）。", is_model_inference=True)],
            )
        ],
        key_conclusions=[Conclusion(text="各有侧重。", sources=[SourceRef(url="https://example.com/x", title="示例")])],
        open_questions=["未来趋势如何？"],
    )
    rec = ResearchRecord(
        research_id="demo-id",
        topic=req.topic,
        status=Status.running,
        created_at="2026-08-17T00:00:00+00:00",
        updated_at="2026-08-17T00:00:00+00:00",
        report=report,
        report_html="<html>...",
        sources=[Source(url="https://example.com/x", title="示例")],
        draft=[block],
        process=ResearchProcess(plan=["OpenAI Agents SDK"], iterations=1),
        confidence=conf,
    )
    dumped = rec.model_dump_json(indent=2, ensure_ascii=False)
    assert len(dumped) > 100
    back = ResearchRecord.model_validate_json(dumped)
    assert back.research_id == "demo-id" and back.report_html == "<html>..."
    print("ResearchRecord 序列化->反序列化 OK，字段数 =", len(back.model_dump()))
    print("models 自检 OK")
