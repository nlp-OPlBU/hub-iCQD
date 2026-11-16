import pytest
from pydantic import ValidationError

from deep_research.models import (
    ExtractedNote, GapDecision, Report, ResearchPlan, RoundRecord,
    SearchResult, SubQuestion, UnsourcedClaim,
)


def test_extracted_note_requires_source_url():
    with pytest.raises(ValidationError):
        ExtractedNote(text="要点", source_title="标题", sub_question_id="sq1")


def test_unsourced_claim_default_label():
    assert UnsourcedClaim(text="推断内容").label == "模型推断"


def test_round_record_with_gap_decision():
    d = GapDecision(need_more=True, reason="缺少数据", extra_queries=["补充检索"])
    r = RoundRecord(round_no=1, sub_question_id="sq1",
                    queries=["q1"], pages_read=10, new_findings=3, gap_decision=d)
    assert r.gap_decision.extra_queries == ["补充检索"]


def test_report_model_validate_dict():
    data = {
        "title": "测试报告",
        "summary": "摘要",
        "sections": [{"heading": "第一节", "body": "内容", "citation_ids": ["1"]}],
        "conclusions": [{"text": "结论", "citation_ids": ["1"], "confidence": "高"}],
        "open_questions": ["问题1"],
        "confidence": {"overall": "中", "info_cutoff_time": "2026-09-10",
                        "unsourced_claims": [{"text": "推断", "label": "模型推断"}]},
    }
    report = Report.model_validate(data)
    assert report.conclusions[0].confidence == "高"
    assert report.confidence.info_cutoff_time == "2026-09-10"
    assert report.confidence.unsourced_claims[0].label == "模型推断"


def test_search_result_and_plan():
    sr = SearchResult(query="天空为什么是蓝色的", title="标题", url="https://a.b/c", snippet="摘要")
    plan = ResearchPlan(
        sub_questions=[SubQuestion(id="sq1", question="子问题")],
        search_queries={"sq1": ["检索词"]},
    )
    assert plan.search_queries["sq1"] == ["检索词"]
    assert sr.snippet == "摘要"
