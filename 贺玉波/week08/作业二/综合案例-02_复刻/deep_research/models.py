from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ConfidenceLevel = Literal["高", "中", "低"]


class SubQuestion(BaseModel):
    id: str
    question: str
    status: str = "pending"
    notes: str = ""


class ResearchPlan(BaseModel):
    sub_questions: list[SubQuestion]
    search_queries: dict[str, list[str]] = Field(default_factory=dict)
    suggested_rounds: int = 1


class SearchResult(BaseModel):
    query: str
    title: str
    url: str
    snippet: str = ""
    date: str = ""  # 资料来源日期（Bocha 提供时透传，用于置信度的信息截止时间）


class ExtractedNote(BaseModel):
    text: str
    source_url: str
    source_title: str
    sub_question_id: str


class GapDecision(BaseModel):
    need_more: bool
    reason: str = ""
    extra_queries: list[str] = Field(default_factory=list)


class RoundRecord(BaseModel):
    round_no: int
    sub_question_id: str
    queries: list[str]
    pages_read: int
    new_findings: int
    gap_decision: GapDecision


class Citation(BaseModel):
    id: str
    title: str
    url: str
    source: str = ""


class Conclusion(BaseModel):
    text: str
    citation_ids: list[str] = Field(default_factory=list)
    confidence: ConfidenceLevel = "中"


class UnsourcedClaim(BaseModel):
    text: str
    label: str = "模型推断"


class Confidence(BaseModel):
    overall: ConfidenceLevel = "中"
    info_cutoff_time: str = ""
    notes: str = ""
    unsourced_claims: list[UnsourcedClaim] = Field(default_factory=list)


class ReportSection(BaseModel):
    heading: str
    body: str
    citation_ids: list[str] = Field(default_factory=list)


class Report(BaseModel):
    title: str
    summary: str
    sections: list[ReportSection]
    conclusions: list[Conclusion]
    open_questions: list[str] = Field(default_factory=list)
    confidence: Confidence = Field(default_factory=Confidence)
    citations: list[Citation] = Field(default_factory=list)
