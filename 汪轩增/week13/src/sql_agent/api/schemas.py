"""HTTP 层的请求 / 响应契约（结果复用 models.WorkflowOutput）。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    question: str = Field(..., min_length=1, description="自然语言查询问题")


class HealthResponse(BaseModel):
    backend: str
    model: str
    summary_model: str
    tables: list[str]
    max_repair_attempts: int
