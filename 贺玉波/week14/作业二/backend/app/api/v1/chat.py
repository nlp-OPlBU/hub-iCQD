"""问答接口：一次性回答 + SSE 流式回答 + 会话历史。"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, model_validator

from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["chat"])

# 用户粘贴的图片按 data URL 传输：4MB 原图 base64 后约 5.5M 字符
MAX_IMAGE_CHARS = 8_000_000


class ChatRequest(BaseModel):
    question: str = Field(default="", max_length=1000, description="图片提问时可为空")
    image: str | None = Field(
        default=None,
        max_length=MAX_IMAGE_CHARS,
        description="用户粘贴/上传的图片，data:image/...;base64 编码",
    )
    session_id: str | None = Field(default=None, description="不传则新建会话")
    top_k: int | None = Field(default=None, ge=1, le=30)
    doc_id: str | None = Field(default=None, description="只在指定文档内检索")
    with_images: bool = True
    stream: bool = False

    @model_validator(mode="after")
    def _validate_input(self) -> "ChatRequest":
        if not self.question.strip() and not self.image:
            raise ValueError("question 与 image 至少提供一个")
        if self.image and not self.image.startswith("data:image/"):
            raise ValueError("image 必须是 data:image/... 的 base64 数据")
        return self


class ChatResponse(BaseModel):
    session_id: str
    answer: str
    citations: list[dict[str, Any]]
    hits: list[dict[str, Any]]
    need_retrieval: bool
    retrieval_query: str | None = Field(default=None, description="实际用于检索的 query（短问题会被改写）")


async def _sse(payload: ChatRequest) -> AsyncIterator[str]:
    async for event, data in chat_service.ask_stream(
        payload.question,
        session_id=payload.session_id,
        top_k=payload.top_k,
        doc_id=payload.doc_id,
        with_images=payload.with_images,
        image=payload.image,
    ):
        body = json.dumps(data, ensure_ascii=False)
        yield f"event: {event}\ndata: {body}\n\n"


@router.post("", response_model=None)
async def chat(payload: ChatRequest) -> ChatResponse | StreamingResponse:
    if payload.stream:
        # 关掉缓冲，保证 token 边生成边到达浏览器
        return StreamingResponse(
            _sse(payload),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    result = await chat_service.ask(
        payload.question,
        session_id=payload.session_id,
        top_k=payload.top_k,
        doc_id=payload.doc_id,
        with_images=payload.with_images,
        image=payload.image,
    )
    return ChatResponse(**result)


@router.get("/sessions")
async def sessions(limit: int = 30) -> dict[str, Any]:
    """历史会话列表：标题、消息数、最后活跃时间。"""
    return {"items": await chat_service.list_sessions(limit)}


@router.get("/{session_id}/history")
async def history(session_id: str) -> dict[str, Any]:
    return {"session_id": session_id, "messages": await chat_service.history(session_id)}


@router.delete("/{session_id}", status_code=204)
async def reset(session_id: str) -> Response:
    await chat_service.reset(session_id)
    return Response(status_code=204)
