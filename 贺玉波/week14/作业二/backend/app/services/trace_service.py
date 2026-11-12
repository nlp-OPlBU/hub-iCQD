"""链路追踪查询：还原一条 trace 的 span 列表并做延迟聚合。

读路径优先 Redis（trace 落库时的同一份数据，TTL 内最快），
缓存过期后回落到 MySQL，保证历史 trace 仍可追溯。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from app.core.exceptions import NotFoundError
from app.infra.cache import redis_client
from app.infra.db import repo_trace


def _started_at(span: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(span["start_ts"])


def summarize(spans: list[dict[str, Any]]) -> dict[str, Any]:
    """节点级延迟聚合：每个 span 名称的次数 / 总耗时 / 均值 / 最大值。"""
    if not spans:
        return {"span_count": 0, "total_ms": 0, "error_count": 0, "slowest": None, "nodes": []}

    start = min(_started_at(item) for item in spans)
    end = max(
        _started_at(item) + timedelta(milliseconds=item["dur_ms"]) for item in spans
    )
    total_ms = int((end - start).total_seconds() * 1000)

    nodes: dict[str, dict[str, Any]] = {}
    for item in spans:
        node = nodes.setdefault(
            item["span"],
            {"span": item["span"], "count": 0, "total_ms": 0, "max_ms": 0, "error_count": 0},
        )
        node["count"] += 1
        node["total_ms"] += item["dur_ms"]
        node["max_ms"] = max(node["max_ms"], item["dur_ms"])
        if item["status"] == "error":
            node["error_count"] += 1

    breakdown = []
    for node in nodes.values():
        breakdown.append({**node, "avg_ms": round(node["total_ms"] / node["count"], 1)})
    breakdown.sort(key=lambda node: node["total_ms"], reverse=True)

    slowest = max(spans, key=lambda item: item["dur_ms"])
    return {
        "span_count": len(spans),
        "total_ms": total_ms,
        "error_count": sum(1 for item in spans if item["status"] == "error"),
        "slowest": {"span": slowest["span"], "dur_ms": slowest["dur_ms"], "ref_id": slowest.get("ref_id")},
        "nodes": breakdown,
    }


def list_traces(limit: int = 20) -> list[dict[str, Any]]:
    return repo_trace.select_recent_traces(limit)


def get_trace(trace_id: str) -> dict[str, Any]:
    spans = redis_client.read_trace_spans(trace_id)
    source = "redis"
    if not spans:
        spans = repo_trace.select_spans(trace_id)
        source = "mysql"
    if not spans:
        raise NotFoundError(f"trace 不存在或已过期: {trace_id}")
    return {"trace_id": trace_id, "source": source, "spans": spans, "summary": summarize(spans)}
