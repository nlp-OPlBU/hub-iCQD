"""JSON 结构化日志。

约定字段：``ts, level, logger, trace_id, event, msg``，其余业务字段放在 ``fields`` 里。
禁止 print，禁止把整段正文写进日志（超过 512 字截断）。
"""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime
from typing import Any

_trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")
_MAX_TEXT = 512


def set_trace_id(trace_id: str) -> object:
    return _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    return _trace_id_var.get()


def reset_trace_id(token: object) -> None:
    _trace_id_var.reset(token)  # type: ignore[arg-type]


def _truncate(text: str, limit: int = _MAX_TEXT) -> str:
    return text if len(text) <= limit else f"{text[:limit]}…(+{len(text) - limit})"


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "trace_id": get_trace_id() or None,
            "event": getattr(record, "event", None),
            "msg": _truncate(record.getMessage()),
        }
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update({key: _truncate(val) if isinstance(val, str) else val for key, val in fields.items()})
        if record.exc_info:
            payload["exc"] = _truncate(self.formatException(record.exc_info), 2000)
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
    # uvicorn 以 log_config=None 启动时不会自带 handler，统一交给 root
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine", "pymilvus", "kafka"):
        logging.getLogger(name).handlers.clear()
        logging.getLogger(name).propagate = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


def log_event(
    logger: logging.Logger,
    event: str,
    msg: str = "",
    *,
    level: int = logging.INFO,
    **fields: Any,
) -> None:
    """业务事件日志：event 名固定，字段随事件变化。"""
    logger.log(level, msg or event, extra={"event": event, "fields": fields})
