from typing import Literal
from typing_extensions import TypedDict

from langgraph.graph import END, START, StateGraph

from .response import generate_comfort, generate_encouragement
from .sentiment import analyze_sentiment


class SentimentGraphState(TypedDict):
    """LangGraph 状态定义，串联进程 1 与进程 2。"""

    user_input: str
    sentiment: Literal["positive", "negative"]
    sentiment_reason: str
    reply: str


def sentiment_node(state: SentimentGraphState) -> dict:
    """节点 1（进程 1）：执行情感识别。"""
    result = analyze_sentiment(state["user_input"])
    return {
        "sentiment": result.sentiment,
        "sentiment_reason": result.reason,
    }


def route_by_sentiment(
    state: SentimentGraphState,
) -> Literal["encourage_node", "comfort_node"]:
    """条件路由：正向走鼓励节点，负面走排解节点。"""
    if state["sentiment"] == "positive":
        return "encourage_node"
    return "comfort_node"


def encourage_node(state: SentimentGraphState) -> dict:
    """节点 2a（进程 2 - 正向）：生成鼓励回复。"""
    reply = generate_encouragement(
        user_input=state["user_input"],
        sentiment_reason=state.get("sentiment_reason", ""),
    )
    return {"reply": reply}


def comfort_node(state: SentimentGraphState) -> dict:
    """节点 2b（进程 2 - 负面）：生成排解回复。"""
    reply = generate_comfort(
        user_input=state["user_input"],
        sentiment_reason=state.get("sentiment_reason", ""),
    )
    return {"reply": reply}


def build_graph():
    """构建并编译 LangGraph 工作流。"""
    builder = StateGraph(SentimentGraphState)

    builder.add_node("sentiment_node", sentiment_node)
    builder.add_node("encourage_node", encourage_node)
    builder.add_node("comfort_node", comfort_node)

    builder.add_edge(START, "sentiment_node")
    builder.add_conditional_edges(
        "sentiment_node",
        route_by_sentiment,
        ["encourage_node", "comfort_node"],
    )
    builder.add_edge("encourage_node", END)
    builder.add_edge("comfort_node", END)

    return builder.compile()


app_graph = build_graph()


def run_sentiment_workflow(user_input: str) -> SentimentGraphState:
    """执行完整的情感识别与回应工作流。"""
    return app_graph.invoke({"user_input": user_input})
