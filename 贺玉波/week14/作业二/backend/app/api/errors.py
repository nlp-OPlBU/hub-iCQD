"""统一异常到错误体的转换。

所有出站错误都长这样：
``{"error": {"code": "...", "message": "...", "trace_id": "..."}}``
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.exceptions import AppError, ErrorCode
from app.core.logging import get_logger, get_trace_id, log_event

logger = get_logger("app.api.errors")

_STATUS_TO_CODE = {
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT_STATE,
    413: ErrorCode.FILE_TOO_LARGE,
    422: ErrorCode.VALIDATION_ERROR,
}


def _body(code: ErrorCode, message: str, status: int) -> JSONResponse:
    trace_id = get_trace_id()
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code.value, "message": message, "trace_id": trace_id}},
        headers={"X-Request-Id": trace_id} if trace_id else None,
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        log_event(
            logger,
            "app_error",
            level=logging.WARNING,
            code=exc.code.value,
            path=request.url.path,
            detail=exc.details,
            msg=exc.message,
        )
        return JSONResponse(
            status_code=exc.http_status,
            content=exc.to_payload(get_trace_id()),
            headers={"X-Request-Id": get_trace_id()} if get_trace_id() else None,
        )

    @app.exception_handler(RequestValidationError)
    async def _handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        detail = "; ".join(f"{'.'.join(map(str, err['loc']))}: {err['msg']}" for err in exc.errors())
        return _body(ErrorCode.VALIDATION_ERROR, detail or "参数不合法", 422)

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL)
        return _body(code, str(exc.detail), exc.status_code)

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        log_event(
            logger,
            "unhandled_exception",
            level=logging.ERROR,
            path=request.url.path,
            error=f"{type(exc).__name__}: {exc}",
            msg="未捕获异常",
        )
        return _body(ErrorCode.INTERNAL, "服务内部错误", 500)
