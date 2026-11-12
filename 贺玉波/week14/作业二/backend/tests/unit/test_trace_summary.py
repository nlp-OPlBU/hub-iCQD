"""trace 延迟聚合的纯函数单测（不依赖任何中间件）。"""

from __future__ import annotations

from app.services.trace_service import summarize


def _span(name: str, start: str, dur_ms: int, status: str = "ok") -> dict:
    return {
        "span": name,
        "parent": None,
        "ref_id": None,
        "status": status,
        "start_ts": start,
        "dur_ms": dur_ms,
        "from_cache": False,
        "attrs": {},
    }


def test_summary_total_uses_wall_clock_window() -> None:
    spans = [
        _span("http_request", "2026-11-09T10:00:00.000", 120),
        _span("qa.retrieve", "2026-11-09T10:00:00.010", 40),
    ]
    summary = summarize(spans)
    assert summary["span_count"] == 2
    assert summary["total_ms"] == 120  # 最早开始到最后结束，而不是各 span 求和
    assert summary["slowest"]["span"] == "http_request"


def test_summary_aggregates_same_span_name() -> None:
    spans = [
        _span("qa.retrieve", "2026-11-09T10:00:00.000", 10),
        _span("qa.retrieve", "2026-11-09T10:00:00.100", 30),
        _span("qa.retrieve", "2026-11-09T10:00:00.200", 20, status="error"),
    ]
    summary = summarize(spans)
    node = summary["nodes"][0]
    assert node["span"] == "qa.retrieve"
    assert node["count"] == 3
    assert node["total_ms"] == 60
    assert node["avg_ms"] == 20.0
    assert node["max_ms"] == 30
    assert node["error_count"] == 1
    assert summary["error_count"] == 1


def test_summary_sorted_by_total_desc() -> None:
    spans = [
        _span("a", "2026-11-09T10:00:00.000", 5),
        _span("b", "2026-11-09T10:00:00.000", 50),
        _span("c", "2026-11-09T10:00:00.000", 20),
    ]
    assert [node["span"] for node in summarize(spans)["nodes"]] == ["b", "c", "a"]


def test_summary_empty() -> None:
    summary = summarize([])
    assert summary == {"span_count": 0, "total_ms": 0, "error_count": 0, "slowest": None, "nodes": []}
