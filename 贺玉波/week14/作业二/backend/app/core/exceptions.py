"""统一错误码与业务异常。

错误码同时是 HTTP 响应体里的 ``error.code`` 和日志里的可 grep 字段。
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT_STATE = "CONFLICT_STATE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"
    PDF_TOO_LARGE = "PDF_TOO_LARGE"
    NO_CONTENT = "NO_CONTENT"
    PARSE_FAILED = "PARSE_FAILED"
    PARSE_TIMEOUT = "PARSE_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MQ_UNAVAILABLE = "MQ_UNAVAILABLE"
    STORAGE_ERROR = "STORAGE_ERROR"
    INTERNAL = "INTERNAL"


HTTP_STATUS: dict[ErrorCode, int] = {
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT_STATE: 409,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.PDF_TOO_LARGE: 422,
    ErrorCode.NO_CONTENT: 422,
    ErrorCode.PARSE_FAILED: 500,
    ErrorCode.PARSE_TIMEOUT: 504,
    ErrorCode.MODEL_UNAVAILABLE: 503,
    ErrorCode.MODEL_TIMEOUT: 504,
    ErrorCode.MQ_UNAVAILABLE: 503,
    ErrorCode.STORAGE_ERROR: 500,
    ErrorCode.INTERNAL: 500,
}


class AppError(Exception):
    """业务异常：由 api 层统一转成 ``{"error": {...}}``。"""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        details: dict[str, Any] | None = None,
        http_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
        self.http_status = http_status or HTTP_STATUS.get(code, 500)

    def to_payload(self, trace_id: str = "") -> dict[str, Any]:
        error: dict[str, Any] = {"code": self.code.value, "message": self.message, "trace_id": trace_id}
        if self.details:
            error["details"] = self.details
        return {"error": error}


class NotFoundError(AppError):
    def __init__(self, message: str = "资源不存在", **kwargs: Any) -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, **kwargs)


class ConflictStateError(AppError):
    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(ErrorCode.CONFLICT_STATE, message, **kwargs)
