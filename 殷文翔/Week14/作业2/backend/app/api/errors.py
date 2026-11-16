import logging
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.schemas.common import ErrorDetail, ErrorResponse
from app.core.logging import request_id_var


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, retryable: bool = False,
                 details: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.retryable, self.details = retryable, details or {}


def error_response(request: Request, status: int, code: str, message: str,
                   retryable: bool = False, details: dict | None = None, headers=None):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    body = ErrorResponse(error=ErrorDetail(code=code, message=message,
                         retryable=retryable, details=details or {}), request_id=request_id)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"),
                        headers={**(headers or {}), "X-Request-ID": request_id})


def install_error_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def business_error(request: Request, exc: ApiError):
        return error_response(request, exc.status, exc.code, exc.message, exc.retryable, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        # Never return input/ctx: they may contain secrets or submitted document text.
        fields = [{"field": ".".join(map(str, err["loc"])), "type": err["type"]} for err in exc.errors()]
        return error_response(request, 422, "VALIDATION_ERROR", "请求参数不合法", details={"fields": fields})

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        return error_response(request, exc.status_code, f"HTTP_{exc.status_code}",
                              "请求无法处理", headers=exc.headers)

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception):
        token = request_id_var.set(getattr(request.state, "request_id", "-"))
        try:
            logging.getLogger(__name__).error("Unhandled request exception: %s", type(exc).__name__)
        finally:
            request_id_var.reset(token)
        return error_response(request, 500, "INTERNAL_ERROR", "服务内部错误")
