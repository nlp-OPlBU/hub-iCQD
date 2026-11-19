from app.emotion import parse_emotion_json
from app.workflow import AgentsEmotionWorkflow


def test_parser_accepts_fenced_json_and_percent_confidence():
    result = parse_emotion_json(
        '```json\n{"label":"积极","confidence":"85%","reason":"分享了好消息"}\n```'
    )

    assert result.label == "positive"
    assert result.confidence == 0.85
    assert "好消息" in result.reason


def test_positive_result_selects_encouragement_agent():
    workflow = object.__new__(AgentsEmotionWorkflow)
    workflow._encourage_agent = "encouragement-agent"
    workflow._relieve_agent = "relief-agent"
    workflow._neutral_agent = "neutral-agent"

    agent, strategy, label = workflow._agent_for("positive")

    assert agent == "encouragement-agent"
    assert strategy == "encouragement"
    assert label == "正向鼓励"


def test_negative_result_selects_relief_agent():
    workflow = object.__new__(AgentsEmotionWorkflow)
    workflow._encourage_agent = "encouragement-agent"
    workflow._relieve_agent = "relief-agent"
    workflow._neutral_agent = "neutral-agent"

    agent, strategy, label = workflow._agent_for("negative")

    assert agent == "relief-agent"
    assert strategy == "relief"
    assert label == "情绪疏导"


def test_ambiguous_result_selects_neutral_agent():
    workflow = object.__new__(AgentsEmotionWorkflow)
    workflow._encourage_agent = "encouragement-agent"
    workflow._relieve_agent = "relief-agent"
    workflow._neutral_agent = "neutral-agent"

    agent, strategy, label = workflow._agent_for("neutral")

    assert agent == "neutral-agent"
    assert strategy == "neutral"
    assert label == "温和陪伴"
