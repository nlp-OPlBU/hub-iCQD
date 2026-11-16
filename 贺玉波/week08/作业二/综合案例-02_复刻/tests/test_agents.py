import pytest

from deep_research.agents import AgentError, AgentRunner
from deep_research.llm import MockLLM
from deep_research.models import SearchResult, SubQuestion

PLAN_JSON = {
    "sub_questions": [{"id": "sq1", "question": "子问题一"}],
    "search_queries": {"sq1": ["检索词一"]},
    "suggested_rounds": 1,
}

READ_JSON = [
    {"text": "要点一", "source_url": "https://a.com/1", "source_title": "来源一"},
]

REFINE_JSON = {"need_more": False, "reason": "够了", "extra_queries": []}

REPORT_JSON = {
    "title": "报告标题",
    "summary": "摘要",
    "sections": [{"heading": "第一节", "body": "内容", "citation_ids": ["1"]}],
    "conclusions": [{"text": "结论", "citation_ids": ["1"], "confidence": "高"}],
    "open_questions": [],
    "confidence": {"overall": "高", "info_cutoff_time": "2026-09-10"},
}


def test_plan_parses_llm_json():
    runner = AgentRunner(MockLLM(responses=[PLAN_JSON]))
    plan = runner.plan("主题")
    assert plan.sub_questions[0].id == "sq1"
    assert plan.search_queries["sq1"] == ["检索词一"]


def test_read_overrides_sub_question_id():
    runner = AgentRunner(MockLLM(responses=[READ_JSON]))
    sq = SubQuestion(id="sq9", question="子问题")
    notes = runner.read(sq, [SearchResult(query="q", title="t", url="https://a.com/1")])
    assert notes[0].sub_question_id == "sq9"


def test_refine_parses_decision():
    runner = AgentRunner(MockLLM(responses=[REFINE_JSON]))
    d = runner.refine(SubQuestion(id="sq1", question="q"), [])
    assert d.need_more is False


def test_synthesize_parses_report():
    runner = AgentRunner(MockLLM(responses=[REPORT_JSON]))
    report = runner.synthesize("主题", [])
    assert report.title == "报告标题"
    assert report.confidence.overall == "高"


def test_plan_raises_agent_error_on_bad_shape():
    runner = AgentRunner(MockLLM(responses=[{"wrong": "shape"}]))
    with pytest.raises(AgentError):
        runner.plan("主题")
