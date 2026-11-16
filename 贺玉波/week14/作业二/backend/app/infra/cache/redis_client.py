"""Redis 客户端：状态投影、会话历史、幂等键、trace 缓存。"""

from __future__ import annotations

import json
import logging
from typing import Any, Sequence

from redis import Redis

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.tracing import SpanRecord

logger = get_logger("app.infra.redis")

_client: Redis | None = None


def get_redis() -> Redis:
    global _client
    if _client is None:
        settings = get_settings().redis
        _client = Redis.from_url(
            settings.url,
            socket_timeout=settings.socket_timeout,
            socket_connect_timeout=settings.socket_connect_timeout,
            decode_responses=True,
            health_check_interval=30,
        )
    return _client


def ping_redis() -> None:
    get_redis().ping()


def push_trace_spans(trace_id: str, spans: Sequence[SpanRecord]) -> None:
    key = f"trace:{trace_id}"
    payload = [json.dumps(item.as_dict(), ensure_ascii=False) for item in spans]
    pipe = get_redis().pipeline()
    pipe.rpush(key, *payload)
    pipe.expire(key, get_settings().redis.trace_ttl_seconds)
    pipe.execute()


def read_trace_spans(trace_id: str) -> list[dict[str, Any]]:
    rows = get_redis().lrange(f"trace:{trace_id}", 0, -1)
    return [json.loads(row) for row in rows]


# ---------------------------------------------------------------- 会话缓存
# 只做 MySQL 的热缓存，丢了可以从 chat_message 回填，因此可以随时 flush。


def read_chat_messages(session_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = get_redis().lrange(f"chat:{session_id}", -limit, -1)
    return [json.loads(row) for row in rows]


def replace_chat_messages(session_id: str, messages: Sequence[dict[str, Any]]) -> None:
    """用数据库里的最近若干轮回填缓存（缓存未命中时调用）。"""
    key = f"chat:{session_id}"
    payload = [
        json.dumps({"role": item["role"], "content": item["content"]}, ensure_ascii=False)
        for item in messages
    ]
    pipe = get_redis().pipeline()
    pipe.delete(key)
    if payload:
        pipe.rpush(key, *payload)
        pipe.ltrim(key, -40, -1)
        pipe.expire(key, get_settings().redis.chat_ttl_seconds)
    pipe.execute()


def clear_chat_session(session_id: str) -> None:
    get_redis().delete(f"chat:{session_id}")


def close_redis() -> None:
    global _client
    if _client is not None:
        try:
            _client.close()
        except Exception as exc:
            logger.warning("redis close failed: %s", exc)
    _client = None


# ---------------------------------------------------------------- 指标快照
# api 与 ingest-worker 是两个进程，worker 周期性把指标快照写到这里，
# api 渲染 /metrics 时读取合并。带 TTL，worker 挂掉后不会留下过期数据。


_METRICS_PREFIX = "metrics:snapshot:"


def write_metrics_snapshot(
    service: str, payload: dict[str, Any], ttl_seconds: int = 30
) -> None:
    get_redis().set(
        f"{_METRICS_PREFIX}{service}", json.dumps(payload, ensure_ascii=False), ex=ttl_seconds
    )


def read_metrics_snapshots() -> list[dict[str, Any]]:
    client = get_redis()
    snapshots: list[dict[str, Any]] = []
    for key in client.scan_iter(match=f"{_METRICS_PREFIX}*", count=10):
        raw = client.get(key)
        if not raw:
            continue
        try:
            snapshots.append(json.loads(raw))
        except json.JSONDecodeError:
            logger.warning("metrics snapshot corrupted: %s", key)
    return snapshots
