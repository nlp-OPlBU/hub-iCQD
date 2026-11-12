"""可观测性中间件：trace_id 贯穿 + 请求级 span 落库。"""

from __future__ import annotations

import asyncio
import logging
import time

from fastapi import FastAPI, Request, Response

from app.core import metrics
from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.tracing import SpanRecord, TraceSession, new_trace_id

logger = get_logger("app.api.access")

# 只有业务 API 落 trace 与指标：静态资源、SPA 首页、探针都不进追踪表，避免把监控表刷满
_TRACE_PREFIX = "/api"
_background: set[asyncio.Task] = set()


def _route_label(request: Request) -> str:
    """用路由模板而不是真实路径，避免 session_id 把标签基数打爆。"""
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


def _record_http(request: Request, status_code: int, dur_ms: int) -> None:
    labels = {"method": request.method, "path": _route_label(request), "status": str(status_code)}
    metrics.inc("rag_http_requests_total", **labels)
    metrics.observe("rag_http_duration_ms", dur_ms, **labels)


def _schedule_flush(session: TraceSession) -> None:
    if not get_settings().trace.persist:
        return
    task = asyncio.create_task(asyncio.to_thread(session.flush))
    _background.add(task)
    task.add_done_callback(_background.discard)


def _skip(path: str) -> bool:
    return not path.startswith(_TRACE_PREFIX)


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def observability(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("X-Request-Id") or new_trace_id()
        started = time.perf_counter()
        session = TraceSession(trace_id)
        with session:
            try:
                response = await call_next(request)
            except Exception as exc:
                session.add(
                    SpanRecord(
                        trace_id=trace_id,
                        span="http_request",
                        ref_id=request.url.path,
                        status="error",
                        dur_ms=int((time.perf_counter() - started) * 1000),
                        attrs={"method": request.method, "path": request.url.path, "error": f"{type(exc).__name__}"},
                    )
                )
                log_event(
                    logger,
                    "http_request",
                    level=logging.ERROR,
                    method=request.method,
                    path=request.url.path,
                    status_code=500,
                    dur_ms=int((time.perf_counter() - started) * 1000),
                )
                if not _skip(request.url.path):
                    _record_http(request, 500, int((time.perf_counter() - started) * 1000))
                    _schedule_flush(session)
                raise

        dur_ms = int((time.perf_counter() - started) * 1000)
        session.add(
            SpanRecord(
                trace_id=trace_id,
                span="http_request",
                ref_id=request.url.path,
                status="ok" if response.status_code < 500 else "error",
                dur_ms=dur_ms,
                attrs={"method": request.method, "path": request.url.path, "status_code": response.status_code},
            )
        )
        response.headers["X-Request-Id"] = trace_id
        log_event(
            logger,
            "http_request",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            dur_ms=dur_ms,
        )
        if not _skip(request.url.path):
            _record_http(request, response.status_code, dur_ms)
            _schedule_flush(session)
        return response
