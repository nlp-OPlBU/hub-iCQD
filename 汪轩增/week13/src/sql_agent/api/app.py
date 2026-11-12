"""FastAPI 入口：把 SQL Agent 暴露为 Web 接口，并托管前端静态文件。"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..config import PROJECT_ROOT, settings
from ..db.catalog import get_catalog
from ..graph.workflow import LangGraphWorkflow
from ..models import WorkflowOutput
from .deps import backend_name, get_workflow
from .schemas import HealthResponse, QueryRequest

logger = logging.getLogger(__name__)

WEB_DIR = PROJECT_ROOT / "web"


def _sse_frame(event_type: str, payload: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def create_app() -> FastAPI:
    app = FastAPI(title="Chinook SQL Agent", version="0.1.0")

    @app.get("/api/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        catalog = get_catalog()
        return HealthResponse(
            backend=backend_name(),
            model=settings.primary_model,
            summary_model=settings.summary_model,
            tables=catalog.table_names,
            max_repair_attempts=settings.max_repair_attempts,
        )

    @app.post("/api/query", response_model=WorkflowOutput)
    async def query(req: QueryRequest) -> WorkflowOutput:
        try:
            return await get_workflow().run(req.question)
        except Exception as exc:  # noqa: BLE001 - 兜底，避免 500 泄漏堆栈
            logger.exception("api query failed")
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/query/stream")
    async def query_stream(req: QueryRequest) -> StreamingResponse:
        workflow = get_workflow()

        if isinstance(workflow, LangGraphWorkflow):
            async def gen() -> AsyncIterator[str]:
                async for event_type, payload in workflow.astream(req.question):
                    yield _sse_frame(event_type, payload)

        else:
            # code 后端无逐节点流，降级为单次 result 事件。
            result = await workflow.run(req.question)

            async def gen() -> AsyncIterator[str]:
                yield _sse_frame("result", result.model_dump())

        return StreamingResponse(gen(), media_type="text/event-stream")

    # 前端静态文件挂在根路径（放在最后，避免覆盖 /api/*）。
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app


app = create_app()
