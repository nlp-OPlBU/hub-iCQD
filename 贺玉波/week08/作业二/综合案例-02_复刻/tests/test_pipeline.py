from deep_research.agents import AgentError
from deep_research.llm import LLMError, LLMParseError, MockLLM
from deep_research.models import ExtractedNote
from deep_research.pipeline import Pipeline, build_citations
from deep_research.search import MockSearchClient, SearchError


def test_build_citations_dedup_by_url():
    notes = [
        ExtractedNote(text="a", source_url="https://x/1", source_title="X1", sub_question_id="sq1"),
        ExtractedNote(text="b", source_url="https://x/1", source_title="X1", sub_question_id="sq1"),
        ExtractedNote(text="c", source_url="https://x/2", source_title="X2", sub_question_id="sq1"),
    ]
    cites = build_citations(notes)
    assert [c.id for c in cites] == ["1", "2"]
    assert cites[0].url == "https://x/1"


def test_pipeline_happy_path_with_refine_loop():
    """mock LLM：补检第一次 true 再 false → 每个子问题 2 轮。"""
    events = []
    pipe = Pipeline(
        llm=MockLLM(), search=MockSearchClient(),
        max_rounds=2, max_total_queries=30,
        on_event=lambda msg: events.append(msg),
    )
    out = pipe.run("测试主题")
    assert out.report.title != ""
    assert out.report.citations  # 由 build_citations 填充
    assert out.total_queries > 0
    assert out.process, "过程记录不能为空"
    # sq1 有初检 + 1 轮补检 = 2 轮
    sq1_rounds = [r for r in out.process if r.sub_question_id == "sq1"]
    assert len(sq1_rounds) == 2
    assert sq1_rounds[0].gap_decision.need_more is True
    assert sq1_rounds[1].gap_decision.need_more is False
    assert events, "进度事件不能为空"


def test_pipeline_planner_failure_degrades():
    class _FailingLLM:
        def __init__(self):
            self.calls = 0

        def chat_json(self, system_prompt, user_prompt, max_retries=3):
            self.calls += 1
            if "规划" in system_prompt:
                raise AgentError("规划失败")
            return MockLLM().chat_json(system_prompt, user_prompt)

    pipe = Pipeline(llm=_FailingLLM(), search=MockSearchClient(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert len(out.process) >= 1


def test_pipeline_search_failure_records_and_continues():
    class _FailingSearch:
        def search(self, query, count=10):
            raise SearchError("网络错误")

    pipe = Pipeline(llm=MockLLM(), search=_FailingSearch(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert out.process
    assert all(r.pages_read == 0 for r in out.process)


def test_pipeline_total_query_budget_enforced():
    class _CountingSearch:
        def __init__(self):
            self.calls = 0

        def search(self, query, count=10):
            self.calls += 1
            return MockSearchClient().search(query)

    s = _CountingSearch()
    pipe = Pipeline(llm=MockLLM(), search=s, max_rounds=5, max_total_queries=3)
    pipe.run("测试主题")
    assert s.calls <= 3


def test_pipeline_synthesize_failure_falls_back():
    class _FailingSynthLLM:
        def chat_json(self, system_prompt, user_prompt, max_retries=3):
            if "综合" in system_prompt:
                raise AgentError("综合失败")
            return MockLLM().chat_json(system_prompt, user_prompt)

    pipe = Pipeline(llm=_FailingSynthLLM(), search=MockSearchClient(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert "降级" in out.report.title
    assert out.report.citations


def test_compute_confidence_tiers():
    """置信度按来源数分级：≥12 高、≥5 中、其余低；无来源日期时截止时间为今天。"""
    from datetime import date
    from deep_research.pipeline import _compute_confidence

    today = date.today().isoformat()
    assert _compute_confidence(12, []).overall == "高"
    assert _compute_confidence(5, []).overall == "中"
    assert _compute_confidence(4, []).overall == "低"
    assert _compute_confidence(0, []).overall == "低"
    assert _compute_confidence(0, []).info_cutoff_time == today


def test_compute_confidence_latest_date():
    from deep_research.pipeline import _compute_confidence

    c = _compute_confidence(10, ["2026-08-01", "2026-09-07", "2026-08-15"])
    assert c.info_cutoff_time == "2026-09-07"


def test_compute_confidence_preserves_unsourced():
    from deep_research.models import UnsourcedClaim
    from deep_research.pipeline import _compute_confidence

    claims = [UnsourcedClaim(text="推断内容")]
    c = _compute_confidence(3, [], unsourced_claims=claims)
    assert c.unsourced_claims == claims


def test_pipeline_confidence_is_deterministic():
    """mock 全流程：置信度由代码计算（2 来源→低；截止取搜索结果最新日期），
    LLM 产出的「模型推断」标注保留。"""
    pipe = Pipeline(llm=MockLLM(), search=MockSearchClient(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert out.report.confidence.overall == "低"
    assert out.report.confidence.info_cutoff_time == "2026-09-01"  # MockSearchClient 的日期
    assert out.report.confidence.unsourced_claims, "LLM 产出的模型推断标注必须保留"


def test_pipeline_llm_error_terminates_with_partial_output():
    """LLM 传输层失败（LLMError）→ 终止循环但保留已生成的过程记录与要点（规格 §4）。"""
    class _TransportFailLLM:
        def chat_json(self, system_prompt, user_prompt, max_retries=3):
            if "规划" in system_prompt:
                return MockLLM().chat_json(system_prompt, user_prompt)  # 规划成功
            raise LLMError("模拟网络中断")

    pipe = Pipeline(llm=_TransportFailLLM(), search=MockSearchClient(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert out.total_queries >= 1          # 检索已发生
    assert out.process                     # 中断轮的过程记录被保留
    assert out.process[-1].gap_decision.reason == "抽取中断"
    assert "中断" in out.report.title      # 报告降级且可输出


def test_pipeline_parse_error_degrades_per_stage():
    """坏 JSON（LLMParseError）→ 阶段降级跑完全程，而不是整体终止。"""
    class _BadJsonLLM:
        def chat_json(self, system_prompt, user_prompt, max_retries=3):
            raise LLMParseError("坏 JSON，重试耗尽")

    pipe = Pipeline(llm=_BadJsonLLM(), search=MockSearchClient(),
                    max_rounds=0, max_total_queries=10)
    out = pipe.run("测试主题")
    assert out.process, "过程记录必须保留"
    assert "中断" not in out.report.title  # 不是传输层终止路径
    assert "降级" in out.report.title      # planner/synthesize 降级兜底
