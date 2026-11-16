"""链路查询接口：按 trace_id 还原 span 瀑布与节点延迟聚合。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path
from pydantic import BaseModel

from app.services import trace_service

router = APIRouter(prefix="/traces", tags=["traces"])


class SpanModel(BaseModel):
    span: str
    parent: str | None = None
    ref_id: str | None = None
    status: str
    start_ts: str
    dur_ms: int
    from_cache: bool = False
    attrs: dict[str, Any] = {}


class NodeModel(BaseModel):
    span: str
    count: int
    total_ms: int
    avg_ms: float
    max_ms: int
    error_count: int


class SlowestModel(BaseModel):
    span: str
    dur_ms: int
    ref_id: str | None = None


class SummaryModel(BaseModel):
    span_count: int
    total_ms: int
    error_count: int
    slowest: SlowestModel | None = None
    nodes: list[NodeModel]


class TraceResponse(BaseModel):
    trace_id: str
    source: str
    spans: list[SpanModel]
    summary: SummaryModel


class TraceItem(BaseModel):
    trace_id: str
    entry: str | None = None
    span_count: int
    total_ms: int
    max_ms: int
    error_count: int
    started_at: str


class TraceListResponse(BaseModel):
    items: list[TraceItem]


@router.get("", response_model=TraceListResponse)
def list_traces(limit: int = 20) -> TraceListResponse:
    """最近的 trace 概览：前端不用手贴 trace_id 也能看到链路。"""
    return TraceListResponse(items=[TraceItem(**item) for item in trace_service.list_traces(limit)])


@router.get("/{trace_id}", response_model=TraceResponse)
def get_trace(trace_id: str = Path(min_length=1, max_length=64)) -> TraceResponse:
    return TraceResponse(**trace_service.get_trace(trace_id))
