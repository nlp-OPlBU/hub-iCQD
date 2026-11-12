"""trace_span 读写。

批量 insert，一次请求 / 一条消息写一次，避免逐条 round-trip；
查询只在 Redis 缓存过期后兜底走 MySQL。
"""

from __future__ import annotations

from typing import Any, Sequence

from sqlalchemy import case, func, insert, select

from app.core.tracing import SpanRecord
from app.infra.db.engine import get_engine
from app.infra.db.models import TraceSpan


def insert_spans(spans: Sequence[SpanRecord]) -> int:
    if not spans:
        return 0
    rows = [
        {
            "trace_id": item.trace_id,
            "span": item.span,
            "parent": item.parent,
            "ref_id": item.ref_id,
            "status": item.status,
            "start_ts": item.start_ts,
            "dur_ms": item.dur_ms,
            "from_cache": int(item.from_cache),
            "attrs": item.attrs or None,
        }
        for item in spans
    ]
    with get_engine().begin() as conn:
        conn.execute(insert(TraceSpan), rows)
    return len(rows)


def select_recent_traces(limit: int = 20) -> list[dict[str, Any]]:
    """最近若干条 trace 的概览，供链路追踪页直接列出，不用手贴 trace_id。

    只列业务 API 请求（/api/*），历史遗留的静态资源 trace 不进列表。
    """
    api_traces = (
        select(TraceSpan.trace_id)
        .where(TraceSpan.span == "http_request", TraceSpan.ref_id.like("/api/%"))
        .scalar_subquery()
    )
    stmt = (
        select(
            TraceSpan.trace_id,
            func.min(TraceSpan.start_ts).label("started_at"),
            # span 数 / 最慢 / 错误数都排除 http_request 容器，和详情页的业务 span 口径一致
            func.sum(case((TraceSpan.span != "http_request", 1), else_=0)).label("span_count"),
            func.sum(TraceSpan.dur_ms).label("sum_ms"),
            # 总耗时取 http_request 容器 span 的时长，避免父子 span 累加重复计数
            func.coalesce(
                func.max(case((TraceSpan.span == "http_request", TraceSpan.dur_ms), else_=None)),
                func.sum(TraceSpan.dur_ms),
            ).label("total_ms"),
            func.coalesce(
                func.max(case((TraceSpan.span != "http_request", TraceSpan.dur_ms), else_=None)),
                0,
            ).label("max_ms"),
            func.sum(
                case(((TraceSpan.status == "error") & (TraceSpan.span != "http_request"), 1), else_=0)
            ).label("error_count"),
            # 入口直接给请求路径（http_request 的 ref_id），比 span 名更能说明这次请求是问答还是检索
            func.coalesce(
                func.max(case((TraceSpan.span == "http_request", TraceSpan.ref_id), else_=None)),
                func.min(TraceSpan.span),
            ).label("entry"),
        )
        .where(TraceSpan.trace_id.in_(api_traces))
        .group_by(TraceSpan.trace_id)
        # 只有 http_request、没有业务 span 的请求（422/404 等直接短路）不占列表位置
        .having(func.sum(case((TraceSpan.span != "http_request", 1), else_=0)) > 0)
        .order_by(func.min(TraceSpan.start_ts).desc())
        .limit(limit)
    )
    with get_engine().begin() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        {
            "trace_id": row["trace_id"],
            "entry": row["entry"],
            "span_count": int(row["span_count"]),
            "total_ms": int(row["total_ms"] or 0),
            "max_ms": int(row["max_ms"] or 0),
            "error_count": int(row["error_count"] or 0),
            "started_at": row["started_at"].isoformat(timespec="seconds"),
        }
        for row in rows
    ]


def select_spans(trace_id: str) -> list[dict[str, Any]]:
    """按开始时间还原一条 trace 的全部 span。"""
    stmt = (
        select(
            TraceSpan.span,
            TraceSpan.parent,
            TraceSpan.ref_id,
            TraceSpan.status,
            TraceSpan.start_ts,
            TraceSpan.dur_ms,
            TraceSpan.from_cache,
            TraceSpan.attrs,
        )
        .where(TraceSpan.trace_id == trace_id)
        .order_by(TraceSpan.start_ts, TraceSpan.id)
    )
    with get_engine().begin() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        {
            "span": row["span"],
            "parent": row["parent"],
            "ref_id": row["ref_id"],
            "status": row["status"],
            "start_ts": row["start_ts"].isoformat(timespec="milliseconds"),
            "dur_ms": row["dur_ms"],
            "from_cache": bool(row["from_cache"]),
            "attrs": row["attrs"] or {},
        }
        for row in rows
    ]
