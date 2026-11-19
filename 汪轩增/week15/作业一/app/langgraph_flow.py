import asyncio
from functools import lru_cache
from typing import Any, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from .shared import (
    API_KEY,
    BASE_URL,
    MODEL,
    REPLY_PROMPTS,
    STRATEGY_BY_LABEL,
    ChatResponse,
    MissingConfigError,
    SentimentResult,
    parse_sentiment_text,
)

BACKEND_NAME = "langgraph"

SENTIMENT_SYSTEM_PROMPT = """你是「情感识别进程」，只负责判断用户消息的情感倾向。
判断标准：
- positive：开心、感激、期待、满意、兴奋等正向情绪；
- negative：难过、焦虑、愤怒、失望、孤独、压力等负面情绪；
- neutral：客观陈述、信息询问，或没有明显情绪色彩。
只输出一个 JSON 对象，不要输出 Markdown 代码块、前后解释或任何其他文字。格式：
{"label": "positive|negative|neutral", "emotion": "具体情绪", "confidence": 0.85, "reason": "判断依据"}"""


class ChatState(TypedDict, total=False):
    """LangGraph 在进程之间流转的状态。"""

    message: str
    sentiment: dict[str, Any]
    strategy: str
    reply: str


@lru_cache(maxsize=4)
def get_llm(temperature: float) -> ChatOpenAI:
    """按需创建 ChatOpenAI 客户端，导入模块时不会因为没有 Key 而失败。"""
    if not API_KEY:
        raise MissingConfigError(
            "未配置 OPENAI_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中填写后重启后端。"
        )
    return ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=temperature,
        timeout=90,
        max_retries=2,
    )


def _content_to_text(content: Any) -> str:
    """把 LangChain 返回的 content（字符串或内容块列表）统一成文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _chat(system_prompt: str, user_prompt: str, temperature: float) -> str:
    llm = get_llm(temperature)
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )
    return _content_to_text(response.content).strip()


def analyze_sentiment(message: str) -> SentimentResult:
    """调用模型做情感识别；格式解析失败时重试一次。"""
    user_prompt = f"请识别下面这条消息的情感：\n{message}"
    last_error: Exception | None = None
    for _ in range(2):
        try:
            raw = _chat(SENTIMENT_SYSTEM_PROMPT, user_prompt, temperature=0.0)
            return parse_sentiment_text(raw)
        except ValueError as exc:
            # 只有“格式不对”才重试；鉴权、网络等错误直接向上抛出
            last_error = exc
    raise RuntimeError(f"情感识别进程没有返回可解析的结果：{last_error}")


def sentiment_node(state: ChatState) -> dict[str, Any]:
    """进程 1 节点：接受请求，输出情感识别结果。"""
    sentiment = analyze_sentiment(state["message"])
    return {"sentiment": sentiment.model_dump()}


def _reply_user_prompt(state: ChatState) -> str:
    sentiment = state["sentiment"]
    return (
        f"用户原话：\n{state['message']}\n\n"
        f"（参考信息：识别情绪为「{sentiment['emotion']}」，{sentiment['reason']}。"
        "不要在回复中提及这段参考信息。）"
    )


def make_reply_node(system_prompt: str, strategy: str):
    """进程 2 的工厂：生成鼓励 / 排解 / 引导三个分支节点。"""

    def reply_node(state: ChatState) -> dict[str, Any]:
        reply = _chat(system_prompt, _reply_user_prompt(state), temperature=0.8)
        if not reply:
            raise RuntimeError("回复生成进程返回了空内容")
        return {"strategy": strategy, "reply": reply}

    return reply_node


def route_by_sentiment(state: ChatState) -> str:
    """情感识别结果 -> 进程 2 的具体分支。"""
    return state["sentiment"]["label"]


def build_graph():
    builder = StateGraph(ChatState)

    builder.add_node("sentiment", sentiment_node)  # 进程 1
    builder.add_node(
        "encourage", make_reply_node(REPLY_PROMPTS["encourage"], "encourage")
    )  # 进程 2-正向
    builder.add_node(
        "comfort", make_reply_node(REPLY_PROMPTS["comfort"], "comfort")
    )  # 进程 2-负面
    builder.add_node(
        "guide", make_reply_node(REPLY_PROMPTS["guide"], "guide")
    )  # 进程 2-中性

    builder.add_edge(START, "sentiment")
    builder.add_conditional_edges(
        "sentiment",
        route_by_sentiment,
        {
            "positive": "encourage",
            "negative": "comfort",
            "neutral": "guide",
        },
    )
    builder.add_edge("encourage", END)
    builder.add_edge("comfort", END)
    builder.add_edge("guide", END)
    return builder.compile()


graph = build_graph()


def _run_sync(message: str) -> ChatResponse:
    state = graph.invoke(
        {"message": message},
        config={"recursion_limit": 10},
    )
    return ChatResponse(
        backend=BACKEND_NAME,
        model=MODEL,
        sentiment=SentimentResult.model_validate(state["sentiment"]),
        strategy=STRATEGY_BY_LABEL[state["sentiment"]["label"]],
        reply=state["reply"],
    )


async def run(message: str) -> ChatResponse:
    """后端调用入口：在线程池中执行同步的 LangGraph，避免阻塞事件循环。"""
    return await asyncio.to_thread(_run_sync, message)
