"""进程1：接收文本，做情感识别（LangGraph 单节点图）。"""

from __future__ import annotations

import logging
from typing import TypedDict

from fastapi import FastAPI
from langgraph.graph import END, START, StateGraph

from llm import LLMClient
from schemas import ClassifyRequest, SentimentResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

llm = LLMClient()


class SentimentState(TypedDict):
    text: str
    result: SentimentResult


async def classify_node(state: SentimentState) -> dict:
    return {"result": await llm.classify(state["text"])}


builder = StateGraph(SentimentState)
builder.add_node("classify", classify_node)
builder.add_edge(START, "classify")
builder.add_edge("classify", END)
graph = builder.compile()

app = FastAPI(title="进程1 · 情感识别（LangGraph）")


@app.post("/classify", response_model=SentimentResult)
async def classify(request: ClassifyRequest) -> SentimentResult:
    output = await graph.ainvoke({"text": request.text})
    return output["result"]


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "sentiment", "framework": "langgraph", "provider": llm.provider}
