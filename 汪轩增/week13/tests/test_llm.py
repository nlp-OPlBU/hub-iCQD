"""LangChain 装配层测试：不依赖 API key，也不发起真实调用。"""

import asyncio

import pytest

from sql_agent.agents import AGENTS, table_selector_agent
from sql_agent.config import settings
from sql_agent.llm import StructuredAgent
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection


def test_agents_import_without_api_key(monkeypatch) -> None:
    """导入期只建提示词模板，不应要求 API key。"""

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert {agent.name for agent in AGENTS} == {
        "TableSelectAgent",
        "SQLGeneratorAgent",
        "SQLRewriteAgent",
        "ResultSummaryAgent",
    }
    assert table_selector_agent.output_type is TableSelection
    assert [agent.output_type for agent in AGENTS] == [
        TableSelection,
        SQLDraft,
        SQLDraft,
        FinalAnswer,
    ]


def test_model_configuration_follows_settings(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = StructuredAgent(
        name="ProbeAgent",
        instructions="只输出结构化结果。",
        output_type=FinalAnswer,
        model=settings.primary_model,
        temperature=0.2,
    )

    model = agent.create_model()

    assert model.model_name == settings.primary_model
    assert model.temperature == 0.2
    assert f"{model.openai_api_base}".rstrip("/") == settings.base_url.rstrip("/")
    assert model.max_retries == settings.llm_max_retries


def test_prompt_template_keeps_system_and_user_parts() -> None:
    agent = StructuredAgent(
        name="ProbeAgent",
        instructions="你是测试助手。",
        output_type=FinalAnswer,
        model="probe-model",
    )

    messages = agent.prompt.format_messages(input="<question>你好</question>")

    # system 提示保留原始指令，并追加"必须调用工具"的输出约定。
    assert messages[0].content.startswith("你是测试助手。")
    assert agent.tool_name in messages[0].content
    assert messages[1].content == "<question>你好</question>"


def test_chain_binds_tools_with_auto_tool_choice(monkeypatch) -> None:
    """思考模式模型只接受 tool_choice=auto，不能被强制指定具体函数。"""

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    agent = StructuredAgent(
        name="ProbeAgent",
        instructions="你是测试助手。",
        output_type=FinalAnswer,
        model="probe-model",
    )

    binding = agent.chain.runner.steps[-1]

    assert binding.kwargs["tool_choice"] == "auto"
    tool = binding.kwargs["tools"][0]
    assert tool["function"]["name"] == "FinalAnswer"
    assert "answer" in tool["function"]["parameters"]["properties"]


def test_resolve_tool_choice_maps_config_values() -> None:
    from sql_agent.llm import _resolve_tool_choice

    assert _resolve_tool_choice(None, "SQLDraft") == "auto"
    assert _resolve_tool_choice("auto", "SQLDraft") == "auto"
    assert _resolve_tool_choice("SQLDraft", "SQLDraft") == {
        "type": "function",
        "function": {"name": "SQLDraft"},
    }
    # 未知取值回退为 auto，避免再次触发 400。
    assert _resolve_tool_choice("typo", "SQLDraft") == "auto"


def test_parse_message_prefers_tool_call_arguments() -> None:
    from langchain_core.messages import AIMessage

    from sql_agent.llm import _parse_message

    message = AIMessage(
        content="",
        tool_calls=[
            {"name": "FinalAnswer", "args": {"answer": "共 347 张专辑"}, "id": "1"}
        ],
    )

    parsed, error = _parse_message(message, FinalAnswer, "FinalAnswer")

    assert error is None
    assert parsed is not None and parsed.answer == "共 347 张专辑"


def test_parse_message_falls_back_to_content_json() -> None:
    from langchain_core.messages import AIMessage

    from sql_agent.llm import _parse_message

    message = AIMessage(content='```json\n{"answer": "42"}\n```')

    parsed, error = _parse_message(message, FinalAnswer, "FinalAnswer")

    assert error is None
    assert parsed is not None and parsed.answer == "42"


def test_parse_message_reports_error_when_nothing_parses() -> None:
    from langchain_core.messages import AIMessage

    from sql_agent.llm import _parse_message

    parsed, error = _parse_message(AIMessage(content="我不知道"), FinalAnswer, "FinalAnswer")

    assert parsed is None
    assert isinstance(error, Exception)


def test_ainvoke_retries_once_on_parse_error(monkeypatch) -> None:
    agent = StructuredAgent(
        name="ProbeAgent",
        instructions="你是测试助手。",
        output_type=FinalAnswer,
        model="probe-model",
    )
    payloads = []

    class FakeChain:
        async def ainvoke(self, values):
            payloads.append(values["input"])
            if len(payloads) == 1:
                return {"parsed": None, "raw": None, "parsing_error": ValueError("bad")}
            return {"parsed": FinalAnswer(answer="好的"), "raw": None}

    monkeypatch.setattr(StructuredAgent, "chain", property(lambda self: FakeChain()))

    result = asyncio.run(agent.ainvoke("问题"))

    assert result.answer == "好的"
    assert len(payloads) == 2
    assert "无法解析" in payloads[1]


def test_ainvoke_raises_when_parse_keeps_failing(monkeypatch) -> None:
    agent = StructuredAgent(
        name="ProbeAgent",
        instructions="你是测试助手。",
        output_type=FinalAnswer,
        model="probe-model",
    )

    class FakeChain:
        async def ainvoke(self, values):
            return {"parsed": None, "raw": None, "parsing_error": ValueError("bad")}

    monkeypatch.setattr(StructuredAgent, "chain", property(lambda self: FakeChain()))

    with pytest.raises(TypeError, match="FinalAnswer"):
        asyncio.run(agent.ainvoke("问题"))
