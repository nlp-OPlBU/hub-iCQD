"""ingest-worker：Kafka 消费循环。

语义是 at-least-once：
- 处理成功才提交 offset；
- 失败先退避重投（seek 回原 offset），超过 max_attempts 再进 DLQ 并置 failed；
- 进程被杀时未提交的消息会被重新投递，配合状态机的崩溃恢复通道从头重跑。
"""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from app.core import metrics
from app.core.config import get_settings
from app.core.exceptions import ErrorCode
from app.core.logging import get_logger, log_event, setup_logging
from app.core.tracing import TraceSession, new_trace_id, span
from app.infra.cache.redis_client import write_metrics_snapshot
from app.infra.db import repo_document as repo
from app.infra.db.engine import session_scope
from app.infra.mq.kafka_client import close_producer, create_consumer, ensure_topics, publish
from app.pipeline import ingest
from app.pipeline.mineru import ParseError

logger = get_logger("app.worker.ingest")

BACKOFF_BASE_S = 0.5
BACKOFF_MAX_S = 8.0
# 指标快照上报间隔：api 的 /metrics 会合并这份快照
METRICS_PUBLISH_INTERVAL_S = 5.0


def _classify(exc: BaseException) -> ErrorCode:
    if isinstance(exc, ParseError):
        return exc.code
    if isinstance(exc, LookupError):
        return ErrorCode.NOT_FOUND
    # ModelUnavailable 等自带 code 的异常直接把错误码透传给前端
    code = getattr(exc, "code", None)
    if isinstance(code, ErrorCode):
        return code
    return ErrorCode.INTERNAL


def _to_dlq(payload: dict[str, Any], exc: BaseException) -> None:
    settings = get_settings().kafka
    doc_id = str(payload.get("doc_id") or "")
    try:
        publish(settings.dlq_topic, doc_id, {**payload, "error": f"{type(exc).__name__}: {exc}"})
    except Exception as publish_exc:  # DLQ 也失败就只能留日志，消息会因未提交而重投
        log_event(logger, "dlq_publish_failed", level=40, ref_id=doc_id, error=str(publish_exc))


def process_message(payload: dict[str, Any]) -> bool:
    """处理一条消息。返回 True 表示需要重投（调用方不要提交 offset）。"""
    doc_id = str(payload.get("doc_id") or "")
    if not doc_id:
        log_event(logger, "message_invalid", level=30, payload=payload)
        return False

    try:
        with span("worker.handle", ref_id=doc_id):
            ingest.process_document(doc_id)
        return False
    except Exception as exc:
        code = _classify(exc)
        if code is ErrorCode.NOT_FOUND:
            # 文档行不存在，重试没有意义，直接进 DLQ
            _to_dlq(payload, exc)
            log_event(logger, "message_dropped", level=40, ref_id=doc_id, error=str(exc))
            return False

        with session_scope() as session:
            attempts = repo.bump_attempts(session, doc_id)

        limit = get_settings().mineru.max_attempts
        if attempts < limit:
            backoff = min(BACKOFF_MAX_S, BACKOFF_BASE_S * (2 ** max(attempts - 1, 0)))
            log_event(
                logger,
                "ingest_retry",
                level=30,
                ref_id=doc_id,
                attempt=attempts,
                limit=limit,
                backoff_s=backoff,
                error=f"{type(exc).__name__}: {exc}",
            )
            time.sleep(backoff)
            return True

        _to_dlq(payload, exc)
        ingest.fail_document(doc_id, code, f"{type(exc).__name__}: {exc}")
        log_event(
            logger,
            "ingest_failed",
            level=40,
            ref_id=doc_id,
            code=code.value,
            attempts=attempts,
            error=f"{type(exc).__name__}: {exc}",
        )
        return False


def _record_lag(consumer: Any) -> None:
    """把消费 lag 暴露到 /metrics；探测失败不影响消费主流程。

    kafka-python 3.x 去掉了 ``get_watermark_offsets``，改用 ``end_offsets``（按分区批量查）。
    """
    try:
        assignments = list(consumer.assignment())
        if not assignments:
            return
        ends = consumer.end_offsets(assignments)
        for partition in assignments:
            high = ends.get(partition)
            if high is None:
                continue
            metrics.set_gauge(
                "rag_kafka_consumer_lag",
                max(0, high - consumer.position(partition)),
                topic=partition.topic,
                partition=str(partition.partition),
            )
    except Exception as exc:  # 指标采集是尽力而为
        log_event(logger, "lag_probe_failed", level=30, error=str(exc))


def _publish_metrics() -> None:
    """把本进程指标镜像到 Redis，api 的 /metrics 会合并展示。

    上报失败只记日志：指标是观测手段，不能反过来影响消费。
    """
    try:
        write_metrics_snapshot(
            "ingest-worker",
            metrics.snapshot(),
            ttl_seconds=int(METRICS_PUBLISH_INTERVAL_S * 6),
        )
    except Exception as exc:
        log_event(logger, "metrics_publish_failed", level=30, error=str(exc))


def run() -> None:
    setup_logging()
    settings = get_settings()
    ensure_topics()
    consumer = create_consumer(topics=[settings.kafka.topic], group_id=settings.kafka.group_id)

    stop = threading.Event()

    def _handle_signal(signum: int, _frame: Any) -> None:
        log_event(logger, "worker_signal", level=30, signal=signal.Signals(signum).name)
        stop.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    log_event(logger, "worker_started", topic=settings.kafka.topic, group=settings.kafka.group_id)
    last_publish = 0.0
    try:
        while not stop.is_set():
            records = consumer.poll(timeout_ms=1000)
            _record_lag(consumer)
            now = time.monotonic()
            if now - last_publish >= METRICS_PUBLISH_INTERVAL_S:
                last_publish = now
                _publish_metrics()
            if not records:
                continue

            requeue = False
            for topic_partition, items in records.items():
                for record in items:
                    payload = record.value if isinstance(record.value, dict) else {}
                    trace_id = str(payload.get("trace_id") or new_trace_id())
                    session = TraceSession(trace_id)
                    with session:
                        with span(
                            "kafka.consume",
                            ref_id=payload.get("doc_id"),
                            partition=record.partition,
                            offset=record.offset,
                        ):
                            retry = process_message(payload)
                    session.flush()

                    if retry:
                        consumer.seek(topic_partition, record.offset)
                        requeue = True
                        break
                    consumer.commit()
                if requeue:
                    break
    finally:
        consumer.close()
        close_producer()
        log_event(logger, "worker_stopped")


if __name__ == "__main__":
    run()
