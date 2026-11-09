from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .chains import ModelContractError
from .config import Settings
from .graph import SqlAgentGraph, WorkflowError
from .models import AnswerDelta, ConversationTurn, ProgressUpdate

logger = logging.getLogger("sql_agent_lc.api")


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2_000)
    conversation_id: str | None = Field(
        default=None,
        pattern=r"^conv_[a-f0-9]{32}$",
    )
    history: list[ConversationTurn] = Field(default_factory=list, max_length=5)


AgentFactory = Callable[[], SqlAgentGraph]


def _sse(event: str, data: Any) -> str:
    if isinstance(data, BaseModel):
        payload = data.model_dump(mode="json")
    else:
        payload = data
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"
    )


def create_app(agent_factory: AgentFactory | None = None) -> FastAPI:
    factory = agent_factory or (lambda: SqlAgentGraph(Settings.from_env()))

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agent = factory()
        yield

    app = FastAPI(
        title="SQL Code Agent API",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/chat/stream")
    async def chat_stream(
        payload: ChatRequest, request: Request
    ) -> StreamingResponse:
        agent: SqlAgentGraph = request.app.state.agent
        request_id = agent.new_request_id()
        conversation_id = payload.conversation_id or agent.new_conversation_id()

        async def events() -> AsyncIterator[str]:
            queue: asyncio.Queue[ProgressUpdate | AnswerDelta] = asyncio.Queue()
            task = asyncio.create_task(
                agent.answer_detailed(
                    payload.question,
                    request_id=request_id,
                    conversation_id=conversation_id,
                    history=payload.history,
                    on_event=queue.put_nowait,
                )
            )
            yield _sse(
                "meta",
                {
                    "request_id": request_id,
                    "conversation_id": conversation_id,
                },
            )

            try:
                while not task.done() or not queue.empty():
                    if await request.is_disconnected():
                        task.cancel()
                        return
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=0.1)
                    except TimeoutError:
                        continue
                    event_name = (
                        "answer_delta"
                        if isinstance(item, AnswerDelta)
                        else "progress"
                    )
                    yield _sse(event_name, item)

                result = await task
                yield _sse("result", result)
            except asyncio.CancelledError:
                task.cancel()
                raise
            except WorkflowError as error:
                yield _sse(
                    "error",
                    {
                        "request_id": request_id,
                        "code": type(error).__name__,
                        "message": error.public_message,
                    },
                )
            except ModelContractError:
                yield _sse(
                    "error",
                    {
                        "request_id": request_id,
                        "code": "ModelContractError",
                        "message": "模型返回格式异常，请重试。",
                    },
                )
            except Exception:
                logger.exception("Unhandled streaming request error")
                yield _sse(
                    "error",
                    {
                        "request_id": request_id,
                        "code": "InternalError",
                        "message": "系统暂时无法完成查询。",
                    },
                )
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return app


app = create_app()


def main() -> None:
    uvicorn.run(
        "langchain_sql_agent.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
