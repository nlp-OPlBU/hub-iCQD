"""Two-stage emotion-support workflow implemented with a LangGraph state graph.

Graph:
    START -> recognize_emotion
                 ├─ positive -> encourage
                 ├─ negative -> relieve
                 └─ neutral  -> accompany
                                            -> END
"""

from typing import Any, Literal, TypedDict

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .emotion import EmotionParseError, parse_emotion_json
from .schemas import ChatResponse, EmotionResult, Strategy

EMOTION_SYSTEM_PROMPT = """你是一个情感识别器（流程 1）。请判断用户当前表达的主要情绪倾向。
只返回一个 JSON 对象，不要使用 Markdown 代码块，不要添加任何额外解释。

JSON 格式：
{"label": "positive|negative|neutral", "confidence": 0.0, "reason": "不超过40字的判断依据"}

判定规则：
- positive：开心、期待、感激、满足、兴奋等积极情绪占主导。
- negative：难过、焦虑、愤怒、孤独、失望、压力等消极情绪占主导。
- neutral：情绪平淡、信息询问，或正负情绪明显混合且无法判断主导。

confidence 必须是 0 到 1 之间的小数，真实反映判断把握。
reason 使用用户所用的语言。"""

ENCOURAGE_SYSTEM_PROMPT = """你是温暖、真诚的鼓励者（流程 2 · 正向分支）。
用户此刻处于积极情绪。请先具体回应他/她分享的好事，让用户感到被看见，再给出真诚的肯定和适度的鼓励。
要求：
1. 不要夸张、说教、堆砌套话，不要进行心理诊断。
2. 可以提出一个帮助用户延续这份好心情的小建议。
3. 使用用户所用的语言，自然口语，2-4 句话。"""

RELIEVE_SYSTEM_PROMPT = """你是温柔、稳定的情绪支持者（流程 2 · 消极分支）。
用户此刻处于消极情绪。请先准确接纳感受、表达理解，不要急着纠正或讲大道理。
要求：
1. 不评判、不否定、不进行心理诊断，不说“这没什么大不了”。
2. 如需给建议，只给 1-2 个当下可执行的小步骤，并尊重用户是否愿意尝试。
3. 如果内容涉及自伤、伤人或紧急危险，温和地建议立即联系可信任的人或当地紧急援助。
4. 使用用户所用的语言，自然口语，2-4 句话。"""

ACCOMPANY_SYSTEM_PROMPT = """你是温暖、克制的陪伴者（流程 2 · 中性分支）。
用户的情绪倾向不明确。请简要复述你听到的重点，表达愿意继续倾听，并用一个开放问题邀请用户多说一点。
要求：
1. 不臆测、不夸张，不进行心理诊断。
2. 使用用户所用的语言，自然口语，2-4 句话。"""


class WorkflowState(TypedDict, total=False):
    message: str
    emotion: dict[str, Any]
    reply: str
    strategy: Strategy
    strategy_label: str


def _message_text(message: Any) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts).strip()
    return str(content).strip()


class EmotionalSupportWorkflow:
    """Builds and runs the LangGraph workflow with an injected chat model."""

    def __init__(self, llm: BaseChatModel) -> None:
        self._llm = llm
        self._graph = self._build_graph()

    def _build_graph(self):
        builder = StateGraph(WorkflowState)
        builder.add_node("recognize_emotion", self._recognize_emotion)
        builder.add_node("encourage", self._encourage)
        builder.add_node("relieve", self._relieve)
        builder.add_node("accompany", self._accompany)

        builder.add_edge(START, "recognize_emotion")
        builder.add_conditional_edges(
            "recognize_emotion",
            self._route_by_emotion,
            {
                "positive": "encourage",
                "negative": "relieve",
                "neutral": "accompany",
            },
        )
        builder.add_edge("encourage", END)
        builder.add_edge("relieve", END)
        builder.add_edge("accompany", END)
        return builder.compile()

    async def _recognize_emotion(self, state: WorkflowState) -> dict[str, Any]:
        message = state["message"]
        prompt = [
            SystemMessage(content=EMOTION_SYSTEM_PROMPT),
            HumanMessage(content=f"请识别这条消息的情绪：\n<message>\n{message}\n</message>"),
        ]

        last_error: Exception | None = None
        for _ in range(2):
            response = await self._llm.ainvoke(prompt)
            try:
                emotion = parse_emotion_json(_message_text(response))
            except EmotionParseError as exc:
                last_error = exc
                prompt = prompt + [
                    AIMessage(content=_message_text(response)),
                    HumanMessage(content="输出格式无效。请只返回符合要求的 JSON 对象，不要返回其他内容。"),
                ]
                continue

            return {
                "emotion": emotion.model_dump(),
                "strategy": self._strategy_for(emotion.label),
                "strategy_label": self._strategy_label(emotion.label),
            }

        raise EmotionParseError("情感识别结果连续两次无法解析") from last_error

    @staticmethod
    def _strategy_for(label: str) -> Strategy:
        if label == "positive":
            return "encouragement"
        if label == "negative":
            return "relief"
        return "neutral"

    @staticmethod
    def _strategy_label(label: str) -> str:
        return {
            "positive": "正向鼓励",
            "negative": "情绪疏导",
            "neutral": "温和陪伴",
        }[label]

    @staticmethod
    def _route_by_emotion(state: WorkflowState) -> Literal["positive", "negative", "neutral"]:
        return state["emotion"]["label"]

    async def _respond(
        self,
        state: WorkflowState,
        system_prompt: str,
    ) -> dict[str, str]:
        emotion = EmotionResult.model_validate(state["emotion"])
        user_input = (
            "用户的原话：\n"
            f"<message>\n{state['message']}\n</message>\n\n"
            "流程 1 的判断：\n"
            f"- 情绪标签：{emotion.label}\n"
            f"- 置信度：{emotion.confidence:.2f}\n"
            f"- 判断依据：{emotion.reason or '未提供'}\n\n"
            "请结合以上信息回复用户。"
        )
        response = await self._llm.ainvoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_input),
            ]
        )
        reply = _message_text(response)
        if not reply:
            raise RuntimeError("模型返回了空回复")
        return {"reply": reply}

    async def _encourage(self, state: WorkflowState) -> dict[str, str]:
        return await self._respond(state, ENCOURAGE_SYSTEM_PROMPT)

    async def _relieve(self, state: WorkflowState) -> dict[str, str]:
        return await self._respond(state, RELIEVE_SYSTEM_PROMPT)

    async def _accompany(self, state: WorkflowState) -> dict[str, str]:
        return await self._respond(state, ACCOMPANY_SYSTEM_PROMPT)

    async def run(self, message: str) -> ChatResponse:
        result = await self._graph.ainvoke({"message": message})
        return ChatResponse(
            reply=result["reply"],
            emotion=EmotionResult.model_validate(result["emotion"]),
            strategy=result["strategy"],
            strategy_label=result["strategy_label"],
        )
