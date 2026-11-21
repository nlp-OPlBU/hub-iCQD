"""进程1：接收文本，用 OpenAI Agents SDK 做情感识别。"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from agent_runtime import AgentRuntime
from schemas import ClassifyRequest, SentimentResult

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

runtime = AgentRuntime()

app = FastAPI(title="进程1 · 情感识别（OpenAI Agents SDK）")


@app.post("/classify", response_model=SentimentResult)
async def classify(request: ClassifyRequest) -> SentimentResult:
    return await runtime.classify(request.text)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "sentiment",
        "framework": "openai-agents",
        "provider": runtime.provider,
    }
