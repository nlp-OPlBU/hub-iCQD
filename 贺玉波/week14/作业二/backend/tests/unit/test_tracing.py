"""span 采集：父子关系、耗时、异常标记。"""

from __future__ import annotations

import time

import pytest

from app.core.tracing import TraceSession, new_trace_id, span


def test_span_tree_and_duration() -> None:
    trace_id = new_trace_id()
    with TraceSession(trace_id) as session:
        with span("outer", ref_id="doc-1"):
            with span("inner"):
                time.sleep(0.02)

    assert session.trace_id == trace_id
    by_name = {item.span: item for item in session.spans}
    assert by_name["outer"].parent is None
    assert by_name["inner"].parent == "outer"
    assert by_name["inner"].ref_id is None
    assert by_name["outer"].ref_id == "doc-1"
    assert by_name["inner"].dur_ms >= 15
    assert all(item.trace_id == trace_id for item in session.spans)


def test_span_error_marks_status_and_reraises() -> None:
    with TraceSession() as session:
        with pytest.raises(ValueError):
            with span("embed_text"):
                raise ValueError("boom")

    record = session.spans[0]
    assert record.status == "error"
    assert "ValueError" in record.attrs["error"]
    assert "boom" in record.attrs["error"]


def test_span_attrs_can_be_extended() -> None:
    with TraceSession() as session:
        with span("retrieve_text") as handle:
            handle.set(hits=8, cache=False)

    assert session.spans[0].attrs["hits"] == 8
    assert session.spans[0].as_dict()["attrs"]["cache"] is False


def test_trace_disabled_stops_collecting(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings.trace, "enabled", False)

    with TraceSession() as session:
        with span("qa.retrieve"):
            pass

    assert session.sampled is False
    assert session.spans == []  # 关闭采集后不占缓冲，也不应落库
    session.flush()  # 不应抛错

