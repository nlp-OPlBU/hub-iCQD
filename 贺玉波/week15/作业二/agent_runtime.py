"""进程1 / 进程2 共用的 Agents 运行时。

DeepSeek 只提供 OpenAI 兼容的 Chat Completions：
- 显式使用 OpenAIChatCompletionsModel + AsyncOpenAI(base_url=...)；
- 实测 DeepSeek 不支持 Agents SDK 的 output_type（response_format=json_schema 返回 400），
  因此改为“提示词要求 JSON + Pydantic 校验 + 失败重试”，而不是 output_type。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import AsyncOpenAI

from config import settings
from schemas import Sentiment, SentimentResult

logger = logging.getLogger("agents")

CLASSIFY_INSTRUCTIONS = """你是情感识别器。只输出一个 JSON 对象，不要 markdown 代码块，不要额外解释：
{"sentiment":"positive|negative|neutral","confidence":0到1之间的小数,"reasoning":"不超过50字的中文理由"}

判断标准：
- positive：明确表达愉悦、满意、期待、感谢等积极情绪；
- negative：明确表达痛苦、压力、失落、焦虑、愤怒等消极情绪；
- neutral：陈述事实、信息不足或情绪不明显。
不要做心理诊断，不要给建议。"""

RESPOND_INSTRUCTIONS = {
    Sentiment.POSITIVE: """你是一位真诚的倾听者。用户刚刚表达了积极情绪，请给出 2-3 句中文鼓励：
- 具体回应他/她提到的内容，而不是空泛地说"你真棒"；
- 肯定其付出或感受，可以给一个轻量的行动建议（如记录、庆祝、休息）；
- 不说教，不夸张，不使用 emoji。""",
    Sentiment.NEGATIVE: """你是一位温和的倾听者。用户刚刚表达了消极情绪，请给出 2-3 句中文排解：
- 先共情，承认他/她的感受是合理的，不评判、不急着讲道理；
- 给一个很小、可执行的建议（喝水、散步五分钟、写下最困扰的一件事）；
- 如果用户流露出自伤或自杀意图，优先建议联系身边可信任的人，并拨打中国大陆心理援助热线 12356。
不要说"想开点""别人更惨"这类话。""",
    Sentiment.NEUTRAL: """你是一位平和的对话者。用户的话情绪不明显，请用 2 句中文回应：
- 简要复述你理解到的内容，表示在听；
- 邀请他/她补充背景或此刻的感受，语气自然，不追问隐私。""",
}

# ---------------- MOCK（与 LangGraph 版保持一致的本地规则） ----------------

_POSITIVE_WORDS = (
    "开心", "高兴", "快乐", "升职", "加薪", "成功", "顺利", "喜欢", "爱", "感谢",
    "棒", "优秀", "期待", "兴奋", "幸福", "满足", "轻松", "通过", "拿到", "赢了",
)
_NEGATIVE_WORDS = (
    "难过", "伤心", "压力", "焦虑", "累", "烦", "失败", "失恋", "痛苦", "崩溃",
    "孤独", "害怕", "生气", "绝望", "抑郁", "委屈", "失眠", "内耗", "被骂", "加班",
)
_NEGATED_POSITIVE = ("不开心", "不高兴", "不顺利", "没成功", "不幸福", "不喜欢", "不期待")


def _mock_classify(text: str) -> SentimentResult:
    lowered = text.lower()
    if any(word in text for word in _NEGATED_POSITIVE):
        return SentimentResult(
            sentiment=Sentiment.NEGATIVE,
            confidence=0.82,
            reasoning="命中否定式积极表达，按负面处理（本地规则）",
        )
    positive_hits = [word for word in _POSITIVE_WORDS if word in lowered]
    negative_hits = [word for word in _NEGATIVE_WORDS if word in lowered]
    if len(positive_hits) > len(negative_hits):
        return SentimentResult(
            sentiment=Sentiment.POSITIVE,
            confidence=0.85,
            reasoning=f"命中积极词：{'、'.join(positive_hits[:3])}（本地规则）",
        )
    if len(negative_hits) > len(positive_hits):
        return SentimentResult(
            sentiment=Sentiment.NEGATIVE,
            confidence=0.85,
            reasoning=f"命中消极词：{'、'.join(negative_hits[:3])}（本地规则）",
        )
    return SentimentResult(
        sentiment=Sentiment.NEUTRAL,
        confidence=0.6,
        reasoning="未命中明显情绪词（本地规则）",
    )


def _mock_reply(text: str, sentiment: Sentiment) -> str:
    snippet = text.strip().replace("\n", " ")[:18]
    if sentiment is Sentiment.POSITIVE:
        return (
            f"看到你说「{snippet}」，能感受到这份开心。"
            "这份状态是你一点点积累来的，值得被认真对待。"
            "可以给自己一个小奖励，也把这份感觉留给接下来的挑战。"
        )
    if sentiment is Sentiment.NEGATIVE:
        return (
            f"听到你说「{snippet}」，先停一下，允许自己不好受。"
            "你不需要马上解决所有事，可以先喝口水、走五分钟，或者把最困扰你的那句话写下来。"
            "如果这种状态持续很久，也可以找身边信任的人聊一聊。"
        )
    return (
        f"收到你说的「{snippet}」。"
        "如果你愿意，可以再多讲一点背景或此刻的感受，我好更具体地回应你。"
    )


def _extract_json(content: str) -> dict[str, Any]:
    start = content.find("{")
    end = content.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"模型输出中没有 JSON：{content[:120]}")
    return json.loads(content[start : end + 1])


class AgentRuntime:
    def __init__(self) -> None:
        self._model: OpenAIChatCompletionsModel | None = None
        self._classifier: Agent | None = None
        self._responders: dict[Sentiment, Agent] = {}

        if settings.use_mock:
            return

        # 不把 trace 发往 OpenAI，避免用 DeepSeek key 时出现 401
        set_tracing_disabled(True)
        client = AsyncOpenAI(
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout,
            max_retries=settings.llm_max_retries,
        )
        self._model = OpenAIChatCompletionsModel(
            model=settings.deepseek_model, openai_client=client
        )
        self._classifier = Agent(
            name="sentiment_agent",
            instructions=CLASSIFY_INSTRUCTIONS,
            model=self._model,
            model_settings=ModelSettings(temperature=0),
        )
        self._responders = {
            sentiment: Agent(
                name=f"{sentiment.value}_agent",
                instructions=instructions,
                model=self._model,
                model_settings=ModelSettings(temperature=0.7),
            )
            for sentiment, instructions in RESPOND_INSTRUCTIONS.items()
        }

    @property
    def provider(self) -> str:
        if self._model is None:
            return "mock"
        return f"deepseek:{settings.deepseek_model}"

    async def classify(self, text: str) -> SentimentResult:
        if self._classifier is None:
            return _mock_classify(text)

        last_error: Exception | None = None
        for attempt in range(2):
            try:
                result = await Runner.run(self._classifier, input=text, max_turns=2)
                return SentimentResult.model_validate(_extract_json(str(result.final_output)))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("情感识别第 %s 次失败：%s", attempt + 1, exc)

        logger.error("情感识别降级为中性：%s", last_error)
        return SentimentResult(
            sentiment=Sentiment.NEUTRAL,
            confidence=0.0,
            reasoning=f"模型输出无法解析，已降级为中性：{type(last_error).__name__}",
        )

    async def generate(self, text: str, sentiment: Sentiment) -> str:
        if not self._responders:
            return _mock_reply(text, sentiment)

        result = await Runner.run(self._responders[sentiment], input=text, max_turns=2)
        return str(result.final_output).strip()
