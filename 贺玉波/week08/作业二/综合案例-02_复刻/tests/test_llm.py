import json

import pytest

from deep_research.llm import DeepSeekClient, LLMError, MockLLM


class _FakeCompletions:
    def __init__(self, content):
        self._content = content

    def create(self, **kwargs):
        class _Choice:
            message = type("M", (), {"content": self._content})()
        class _Resp:
            choices = [_Choice()]
        return _Resp()


class _FakeClient:
    def __init__(self, content):
        self.chat = type("C", (), {"completions": _FakeCompletions(content)})()


def test_chat_json_parses_json():
    fake = _FakeClient(json.dumps({"answer": 42}))
    c = DeepSeekClient(api_key="sk-test", client=fake)
    assert c.chat_json("sys", "user") == {"answer": 42}


def test_chat_json_retries_on_bad_json():
    fake = _FakeClient("这不是JSON")
    c = DeepSeekClient(api_key="sk-test", client=fake)
    with pytest.raises(LLMError):
        c.chat_json("sys", "user", max_retries=2)


def test_chat_json_drops_reasoning_effort_on_bad_request():
    calls = []
    # openai 2.x 的 APIStatusError 构造需要真实 httpx.Response（会解引用 .request）
    request = __import__("httpx").Request("POST", "https://api.deepseek.com/chat/completions")
    response = __import__("httpx").Response(400, request=request)

    class _C:
        def create(self, **kwargs):
            calls.append(kwargs)
            raise __import__("openai").BadRequestError(
                "reasoning_effort 参数不支持", response=response, body=None)

    class _Client:
        chat = type("C", (), {"completions": _C()})()

    c = DeepSeekClient(api_key="sk-test", reasoning_effort="max", client=_Client())
    with pytest.raises(LLMError):
        c.chat_json("sys", "user")
    assert "reasoning_effort" not in calls[-1]


def test_mock_llm_dispatch_and_refine_transition():
    m = MockLLM()
    plan = m.chat_json("你是规划器", "主题")
    assert len(plan["sub_questions"]) == 2
    refine1 = m.chat_json("你是补检判断器", "")
    refine2 = m.chat_json("你是补检判断器", "")
    assert refine1["need_more"] is True
    assert refine2["need_more"] is False


def test_mock_llm_explicit_queue():
    m = MockLLM(responses=[{"a": 1}, {"b": 2}])
    assert m.chat_json("任意", "") == {"a": 1}
    assert m.chat_json("任意", "") == {"b": 2}
    with pytest.raises(LLMError):
        m.chat_json("任意", "")


def test_mock_llm_dispatch_with_real_agent_prompts():
    """回归（Task 6 集成发现）：真实四阶段 system prompt 必须命中正确夹具。

    REFINER 提示词含「已抽取要点」，若「抽取」分支在前会把补检请求
    误分发为 DEFAULT_READ。"""
    from deep_research.agents import (
        PLANNER_SYSTEM, READER_SYSTEM, REFINER_SYSTEM, SYNTHESIZER_SYSTEM)

    m = MockLLM()
    assert "sub_questions" in m.chat_json(PLANNER_SYSTEM, "主题")
    assert isinstance(m.chat_json(READER_SYSTEM, "子问题"), list)
    assert "need_more" in m.chat_json(REFINER_SYSTEM, "")
    assert "title" in m.chat_json(SYNTHESIZER_SYSTEM, "")
