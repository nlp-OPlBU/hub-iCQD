"""HTTP 层数据契约；查询结果复用 sql_agent.models.WorkflowOutput。"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: Annotated[
        str,
        StringConstraints(strict=True, strip_whitespace=True, min_length=1, max_length=2000),
    ] = Field(description="自然语言问题，去除首尾空白后为 1～2000 个字符")


class LivenessResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready"] = "ready"


class ErrorDetail(BaseModel):
    field: str
    message: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    error: ErrorBody


ERROR_RESPONSES = {
    415: {"model": ErrorResponse, "description": "请求必须使用 JSON 媒体类型"},
    422: {"model": ErrorResponse, "description": "JSON 或请求字段校验失败"},
    500: {"model": ErrorResponse, "description": "工作流失败或服务内部异常"},
    503: {"model": ErrorResponse, "description": "工作流或数据库尚不可用"},
}
