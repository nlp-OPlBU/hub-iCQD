"""统一 HTTP 错误；响应不包含原始异常、请求体或上游响应。"""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from .schemas import ErrorBody, ErrorDetail, ErrorResponse


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def error_response(
    status_code: int,
    code: str,
    message: str,
    details: list[ErrorDetail] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload = ErrorResponse(error=ErrorBody(
        code=code, message=message, details=details or [],
    ))
    return JSONResponse(payload.model_dump(), status_code=status_code, headers=headers)


async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
    return error_response(exc.status_code, exc.code, exc.message)


async def handle_validation_error(
    request: Request, exc: RequestValidationError,
) -> JSONResponse:
    messages = {
        "missing": "缺少必填字段。",
        "string_type": "问题必须是字符串。",
        "string_too_short": "问题不能为空。",
        "string_too_long": "问题不能超过 2000 个字符。",
        "extra_forbidden": "不允许额外字段。",
        "json_invalid": "请求体不是合法 JSON。",
    }
    # 不使用 input/ctx 或完整错误文本，避免回显用户输入和内部信息。
    details = [
        ErrorDetail(
            field="question" if error["loc"] == ("body", "question") else "body",
            message=messages.get(error["type"], "请求体或字段格式不正确。"),
        )
        for error in exc.errors()
    ]
    return error_response(422, "validation_error", "请求参数不合法。", details)


async def handle_http_error(request: Request, exc: HTTPException) -> JSONResponse:
    # FastAPI 在请求体无法解码时返回 400；按本接口的非法 JSON 约定转为 422。
    status = 422 if exc.status_code == 400 else exc.status_code
    code, message = {
        404: ("not_found", "接口不存在。"),
        405: ("method_not_allowed", "请求方法不支持。"),
        422: ("validation_error", "请求参数不合法。"),
    }.get(status, ("http_error", "HTTP 请求失败。"))
    return error_response(status, code, message, headers=exc.headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(APIError, handle_api_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
