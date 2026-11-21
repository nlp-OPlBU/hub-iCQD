"""进程2：拿到情感标签后，正向鼓励 / 负面排解 / 中性回应。

LangGraph 条件边体现路由：positive → encourage，negative → soothe，neutral → neutral。
"""

from __future__ import annotations

import logging
from typing import TypedDict

from fastapi import FastAPI
from langgraph.graph import END, START, StateGraph

from llm import LLMClient
from schemas import RespondRequest, RespondResponse, Sentiment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

llm = LLMClient()


class RespondState(TypedDict):
    text: str
    sentiment: Sentiment
    reply: str


async def encourage_node(state: RespondState) -> dict:
    return {"reply": await llm.generate(state["text"], Sentiment.POSITIVE)}


async def soothe_node(state: RespondState) -> dict:
    return {"reply": await llm.generate(state["text"], Sentiment.NEGATIVE)}


async def neutral_node(state: RespondState) -> dict:
    return {"reply": await llm.generate(state["text"], Sentiment.NEUTRAL)}


async def dispatch_node(state: RespondState) -> dict:
    return {}


def route_by_sentiment(state: RespondState) -> str:
    return state["sentiment"].value


builder = StateGraph(RespondState)
builder.add_node("dispatch", dispatch_node)
builder.add_node("encourage", encourage_node)
builder.add_node("soothe", soothe_node)
builder.add_node("neutral", neutral_node)
builder.add_edge(START, "dispatch")
builder.add_conditional_edges(
    "dispatch",
    route_by_sentiment,
    {"positive": "encourage", "negative": "soothe", "neutral": "neutral"},
)
builder.add_edge("encourage", END)
builder.add_edge("soothe", END)
builder.add_edge("neutral", END)
graph = builder.compile()

app = FastAPI(title="进程2 · 鼓励/排解（LangGraph）")


@app.post("/respond", response_model=RespondResponse)
async def respond(request: RespondRequest) -> RespondResponse:
    output = await graph.ainvoke({"text": request.text, "sentiment": request.sentiment})
    return RespondResponse(reply=output["reply"])


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "response", "framework": "langgraph", "provider": llm.provider}
