"""全流程节点延迟采集。

设计要点：
- ``TraceSession`` 绑定一个 trace_id 与一份 span 缓冲，在 HTTP 请求 / Kafka 消息边界开启；
- ``span()`` 上下文器负责计时、父子关系、状态与慢节点告警；
- 结束时 ``flush()`` 批量落 MySQL + Redis，落库失败只告警，不影响主流程。
"""

from __future__ import annotations

import logging
import random
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator

from app.core import metrics
from app.core.logging import get_logger, get_trace_id, log_event, reset_trace_id, set_trace_id

logger = get_logger("app.trace")

_spans_var: ContextVar[list["SpanRecord"] | None] = ContextVar("spans", default=None)
_stack_var: ContextVar[tuple[str, ...]] = ContextVar("span_stack", default=())


def new_trace_id() -> str:
    return uuid.uuid4().hex[:32]


@dataclass
class SpanRecord:
    trace_id: str
    span: str
    parent: str | None = None
    ref_id: str | None = None
    status: str = "ok"
    start_ts: datetime = field(default_factory=datetime.now)
    dur_ms: int = 0
    from_cache: bool = False
    attrs: dict[str, Any] = field(default_factory=dict)

    def set(self, **kwargs: Any) -> "SpanRecord":
        self.attrs.update(kwargs)
        return self

    def as_dict(self) -> dict[str, Any]:
        return {
            "span": self.span,
            "parent": self.parent,
            "ref_id": self.ref_id,
            "status": self.status,
            "start_ts": self.start_ts.isoformat(timespec="milliseconds"),
            "dur_ms": self.dur_ms,
            "from_cache": self.from_cache,
            "attrs": self.attrs,
        }


class TraceSession:
    """一次请求 / 一条消息的 span 收集器。"""

    def __init__(self, trace_id: str | None = None) -> None:
        from app.core.config import get_settings

        self.trace_id = trace_id or new_trace_id()
        self.spans: list[SpanRecord] = []
        # 采样决策在会话创建时一次性做出：enabled=false 或未命中 sample_rate 时
        # span 仍会计时（慢节点告警不受影响），但不落库、不占缓冲。
        trace_settings = get_settings().trace
        self.sampled = trace_settings.enabled and (
            trace_settings.sample_rate >= 1.0 or random.random() < trace_settings.sample_rate
        )
        self._trace_token: Any = None
        self._spans_token: Any = None
        self._stack_token: Any = None

    def add(self, record: SpanRecord) -> None:
        if self.sampled:
            self.spans.append(record)

    def __enter__(self) -> "TraceSession":
        self._trace_token = set_trace_id(self.trace_id)
        self._spans_token = _spans_var.set(self.spans if self.sampled else None)
        self._stack_token = _stack_var.set(())
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._stack_token is not None:
            _stack_var.reset(self._stack_token)
        if self._spans_token is not None:
            _spans_var.reset(self._spans_token)
        if self._trace_token is not None:
            reset_trace_id(self._trace_token)

    def flush(self) -> None:
        if not self.sampled or not self.spans:
            return
        from app.core.config import get_settings

        if not get_settings().trace.persist:
            return
        from app.infra.cache.redis_client import push_trace_spans
        from app.infra.db.repo_trace import insert_spans

        try:
            insert_spans(self.spans)
        except Exception as exc:  # 观测数据不能影响主流程
            log_event(logger, "trace_persist_failed", level=logging.WARNING, error=str(exc), trace=self.trace_id)
        try:
            push_trace_spans(self.trace_id, self.spans)
        except Exception as exc:
            log_event(logger, "trace_cache_failed", level=logging.WARNING, error=str(exc), trace=self.trace_id)


def current_span() -> str | None:
    stack = _stack_var.get()
    return stack[-1] if stack else None


@contextmanager
def span(name: str, *, ref_id: str | None = None, **attrs: Any) -> Iterator[SpanRecord]:
    """记录一个节点的耗时；异常时标记 error 并继续抛出。"""
    from app.core.config import get_settings

    stack = _stack_var.get()
    record = SpanRecord(
        trace_id=get_trace_id(),
        span=name,
        parent=stack[-1] if stack else None,
        ref_id=ref_id,
        attrs=dict(attrs),
    )
    _stack_var.set((*stack, name))
    started = time.perf_counter()
    try:
        yield record
    except BaseException as exc:
        record.status = "error"
        record.attrs.setdefault("error", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        _stack_var.set(stack)
        record.dur_ms = int((time.perf_counter() - started) * 1000)
        # 进程内直方图：/metrics 用，和落库的 span 明细互补（一个看分布，一个看单次）
        metrics.observe(
            "rag_span_duration_ms", record.dur_ms, span=record.span, status=record.status
        )
        buffer = _spans_var.get()
        if buffer is not None:
            buffer.append(record)
        if record.dur_ms > get_settings().trace.slow_node_ms:
            log_event(
                logger,
                "slow_span",
                level=logging.WARNING,
                span=record.span,
                dur_ms=record.dur_ms,
                ref_id=record.ref_id,
            )
