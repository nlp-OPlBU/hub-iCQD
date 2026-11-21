import asyncio

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.emotion import parse_emotion_json
from app.workflow import EmotionalSupportWorkflow


def _run(responses: list[AIMessage], message: str):
    llm = FakeMessagesListChatModel(responses=responses)
    return asyncio.run(EmotionalSupportWorkflow(llm).run(message))


def test_parser_accepts_fenced_json_and_percent_confidence():
    result = parse_emotion_json(
        '```json\n{"label":"积极","confidence":"85%","reason":"分享了好消息"}\n```'
    )

    assert result.label == "positive"
    assert result.confidence == 0.85
    assert "好消息" in result.reason


def test_positive_message_takes_encouragement_branch():
    result = _run(
        [
            AIMessage(
                content='{"label":"positive","confidence":0.96,"reason":"项目上线带来的成就感"}'
            ),
            AIMessage(content="项目终于上线，太为你高兴了！这份投入值得被好好庆祝一下。"),
        ],
        "项目终于上线了，特别开心！",
    )

    assert result.emotion.label == "positive"
    assert result.strategy == "encouragement"
    assert result.strategy_label == "正向鼓励"
    assert "高兴" in result.reply


def test_negative_message_takes_relief_branch():
    result = _run(
        [
            AIMessage(
                content='{"label":"negative","confidence":0.9,"reason":"表达了焦虑和压力"}'
            ),
            AIMessage(content="听起来最近的压力确实压得有点喘不过气，你愿意的话可以慢慢说。"),
        ],
        "最近事情好多，有点焦虑。",
    )

    assert result.emotion.label == "negative"
    assert result.strategy == "relief"
    assert result.strategy_label == "情绪疏导"
    assert "压力" in result.reply


def test_ambiguous_message_takes_neutral_branch():
    result = _run(
        [
            AIMessage(
                content='{"label":"neutral","confidence":0.6,"reason":"情绪表达较平淡"}'
            ),
            AIMessage(content="谢谢你和我说这些。今天还有什么想多聊聊的吗？"),
        ],
        "今天很平静，没什么特别的。",
    )

    assert result.strategy == "neutral"
    assert result.strategy_label == "温和陪伴"
