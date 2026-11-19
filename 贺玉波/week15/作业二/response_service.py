"""进程2：拿到情感标签后，用 OpenAI Agents SDK 做鼓励 / 排解。

三个 Agent 之间不使用 handoff：路由由确定性代码完成，模型只负责生成内容。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI

from agent_runtime import AgentRuntime
from schemas import RespondRequest, RespondResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

runtime = AgentRuntime()

app = FastAPI(title="进程2 · 鼓励/排解（OpenAI Agents SDK）")


@app.post("/respond", response_model=RespondResponse)
async def respond(request: RespondRequest) -> RespondResponse:
    reply = await runtime.generate(request.text, request.sentiment)
    return RespondResponse(reply=reply)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "response",
        "framework": "openai-agents",
        "provider": runtime.provider,
    }
