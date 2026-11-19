"""Two-process emotion-support pipeline built with the OpenAI Agents SDK.

Process 1: an emotion-recognition Agent returns structured JSON.
Process 2: one of three specialist Agents is selected from that result and
           generates an encouraging, comforting, or accompanying reply.

The SDK is pointed at DeepSeek's OpenAI-compatible Chat Completions endpoint,
so no OpenAI account or Responses API is required.
"""

from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    RunConfig,
    Runner,
    set_tracing_disabled,
)
from openai import AsyncOpenAI

from .config import Settings, get_settings
from .emotion import EmotionParseError, parse_emotion_json
from .schemas import ChatResponse, EmotionResult, Strategy

EMOTION_INSTRUCTIONS = """你是流程 1：情感识别器。请判断用户当前表达的主要情绪倾向。
只返回一个 JSON 对象，不要使用 Markdown 代码块，不要添加任何额外解释。

JSON 格式：
{"label": "positive|negative|neutral", "confidence": 0.0, "reason": "不超过40字的判断依据"}

判定规则：
- positive：开心、期待、感激、满足、兴奋等积极情绪占主导。
- negative：难过、焦虑、愤怒、孤独、失望、压力等消极情绪占主导。
- neutral：情绪平淡、信息询问，或正负情绪明显混合且无法判断主导。

confidence 必须是 0 到 1 之间的小数，真实反映判断把握。
reason 使用用户所用的语言。"""

ENCOURAGE_INSTRUCTIONS = """你是流程 2 的鼓励 Agent，只负责正向情绪分支。
用户此刻处于积极情绪。请先具体回应他/她分享的好事，让用户感到被看见，再给出真诚的肯定和适度的鼓励。
要求：
1. 不要夸张、说教、堆砌套话，不要进行心理诊断。
2. 可以提出一个帮助用户延续这份好心情的小建议。
3. 使用用户所用的语言，自然口语，2-4 句话。"""

RELIEVE_INSTRUCTIONS = """你是流程 2 的情绪疏导 Agent，只负责消极情绪分支。
用户此刻处于消极情绪。请先准确接纳感受、表达理解，不要急着纠正或讲大道理。
要求：
1. 不评判、不否定、不进行心理诊断，不说“这没什么大不了”。
2. 如需给建议，只给 1-2 个当下可执行的小步骤，并尊重用户是否愿意尝试。
3. 如果内容涉及自伤、伤人或紧急危险，温和地建议立即联系可信任的人或当地紧急援助。
4. 使用用户所用的语言，自然口语，2-4 句话。"""

NEUTRAL_INSTRUCTIONS = """你是流程 2 的陪伴 Agent，只负责中性情绪分支。
用户的情绪倾向不明确。请简要复述你听到的重点，表达愿意继续倾听，并用一个开放问题邀请用户多说一点。
要求：
1. 不臆测、不夸张，不进行心理诊断。
2. 使用用户所用的语言，自然口语，2-4 句话。"""


class LLMNotConfiguredError(RuntimeError):
    """Raised when the DeepSeek API key is missing."""


class AgentsEmotionWorkflow:
    """Runs the two Agent stages sequentially with a DeepSeek-backed model."""

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        api_key = settings.deepseek_api_key.get_secret_value().strip()
        if not api_key:
            raise LLMNotConfiguredError(
                "尚未配置 DEEPSEEK_API_KEY。请复制 backend/.env.example 为 backend/.env 并填写密钥。"
            )

        self._settings = settings

        # DeepSeek exposes Chat Completions, not OpenAI's Responses API.
        # OpenAIChatCompletionsModel keeps the Agents SDK on the compatible path.
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=settings.deepseek_base_url,
            timeout=settings.llm_timeout_seconds,
        )
        self._model = OpenAIChatCompletionsModel(
            model=settings.deepseek_model,
            openai_client=self._client,
        )
        model_settings = ModelSettings(
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout_seconds,
        )

        def make_agent(name: str, instructions: str) -> Agent:
            return Agent(
                name=name,
                instructions=instructions,
                model=self._model,
                model_settings=model_settings,
            )

        self._emotion_agent = make_agent("emotion_recognition", EMOTION_INSTRUCTIONS)
        self._encourage_agent = make_agent("encouragement", ENCOURAGE_INSTRUCTIONS)
        self._relieve_agent = make_agent("emotional_relief", RELIEVE_INSTRUCTIONS)
        self._neutral_agent = make_agent("neutral_companion", NEUTRAL_INSTRUCTIONS)

        # This project talks to DeepSeek only; never upload traces to OpenAI.
        set_tracing_disabled(True)
        self._run_config = RunConfig(
            tracing_disabled=True,
            workflow_name="deepseek-emotion-support",
        )

    @staticmethod
    def _text(value: Any) -> str:
        return value.strip() if isinstance(value, str) else str(value or "").strip()

    def _agent_for(self, label: str) -> tuple[Agent, Strategy, str]:
        if label == "positive":
            return self._encourage_agent, "encouragement", "正向鼓励"
        if label == "negative":
            return self._relieve_agent, "relief", "情绪疏导"
        return self._neutral_agent, "neutral", "温和陪伴"

    async def _run(self, agent: Agent, user_input: str) -> str:
        result = await Runner.run(
            agent,
            input=user_input,
            max_turns=self._settings.agent_max_turns,
            run_config=self._run_config,
        )
        output = self._text(result.final_output)
        if not output:
            raise RuntimeError("Agent 返回了空回复")
        return output

    async def _recognize_emotion(self, message: str) -> EmotionResult:
        user_input = f"请识别这条消息的情绪：\n<message>\n{message}\n</message>"
        raw = await self._run(self._emotion_agent, user_input)

        try:
            return parse_emotion_json(raw)
        except EmotionParseError:
            # One corrective retry keeps the pipeline resilient to malformed JSON.
            raw = await self._run(
                self._emotion_agent,
                f"{user_input}\n\n上一次输出格式无效。请只返回符合要求的 JSON 对象。",
            )
            return parse_emotion_json(raw)

    @staticmethod
    def _support_input(message: str, emotion: EmotionResult) -> str:
        return (
            "用户的原话：\n"
            f"<message>\n{message}\n</message>\n\n"
            "流程 1 的判断：\n"
            f"- 情绪标签：{emotion.label}\n"
            f"- 置信度：{emotion.confidence:.2f}\n"
            f"- 判断依据：{emotion.reason or '未提供'}\n\n"
            "请结合以上信息回复用户。"
        )

    async def run(self, message: str) -> ChatResponse:
        # Process 1: emotion recognition.
        emotion = await self._recognize_emotion(message)

        # Process 2: select the branch Agent from process 1's result.
        agent, strategy, strategy_label = self._agent_for(emotion.label)
        reply = await self._run(agent, self._support_input(message, emotion))

        return ChatResponse(
            reply=reply,
            emotion=emotion,
            strategy=strategy,
            strategy_label=strategy_label,
        )

    async def aclose(self) -> None:
        await self._client.close()
