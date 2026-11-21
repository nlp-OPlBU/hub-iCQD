from langgraph.graph import END, START, StateGraph

from .state import ChatState
from ..config import get_settings, AnalyzeResult

config = get_settings()
import requests

builder = StateGraph(ChatState)


def classifier_node(state: ChatState) -> dict[str, object]:
    response = requests.post(config.sentiment_analyzer_request_url, json={'input': state.user_input}, timeout=30)
    response.raise_for_status()
    analyze_result = AnalyzeResult.model_validate(response.json())
    return {"classifier_result": analyze_result}


def response_positive_node(state: ChatState) -> dict[str, object]:
    response = requests.post(config.response_request_url,
                             json={'input': state.user_input, 'is_positive': True}, timeout=30)
    response.raise_for_status()
    analyze_result = response.json()
    return {
        "response_text": analyze_result,
    }


def response_negative_node(state: ChatState) -> dict[str, object]:
    response = requests.post(config.response_request_url,
                             json={'input': state.user_input, 'is_positive': False}, timeout=30)
    response.raise_for_status()
    analyze_result = response.json()
    return {
        "response_text": analyze_result,
    }


def node_route(state: ChatState) -> str:
    if state.classifier_result.result == "positive":
        return "positive"
    if state.classifier_result.result == "negative":
        return "negative"
    return "error"


builder.add_node("classifier_node", classifier_node)
builder.add_node("response_positive_node", response_positive_node)
builder.add_node("response_negative_node", response_negative_node)

builder.add_edge(START, "classifier_node")
builder.add_conditional_edges(
    "classifier_node",
    node_route,
    {
        "positive": "response_positive_node",
        "negative": "response_negative_node",
        "error": END  # 也可以将 END 映射为结束
    }
)
builder.add_edge("response_positive_node", END)
builder.add_edge("response_negative_node", END)
