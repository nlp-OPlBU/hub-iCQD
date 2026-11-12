"""Recoverable PDF task worker.

Claiming, heartbeats, retries and results are conditional on the persisted lease
token and on the document's latest version. Kafka offsets are committed by the
worker loop only after the corresponding MySQL transaction commits.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import uuid
from datetime import datetime

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, session_factory
from app.schemas.document import ParseEvent
from app.repositories.task import (TaskClaim, claim_task, complete_task, fail_task,
                                   heartbeat, schedule_retry)
from app.services.kafka_consumer import TaskConsumer, create_consumer
from app.services.task_processing import TaskProcessingError, TaskProcessor, TaskResult

logger = logging.getLogger(__name__)


class LeaseHeartbeat:
    """Renew one task lease from a daemon thread while PDF processing runs."""

    def __init__(self, settings: Settings, factory, claim: TaskClaim):
        self.settings, self.factory, self.claim = settings, factory, claim
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{claim.task_id}",
                                        daemon=True)
        self.lost = False

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *_):
        self._stop.set()
        self._thread.join(timeout=max(self.settings.task_heartbeat_seconds * 2, 1))
        return False

    def _run(self):
        while not self._stop.wait(self.settings.task_heartbeat_seconds):
            try:
                with self.factory() as session, session.begin():
                    if not heartbeat(session, self.claim, settings=self.settings):
                        self.lost = True
                        logger.warning("Lease lost task_id=%s", self.claim.task_id)
                        return
            except Exception:
                # A transient database failure must not kill parsing; the lease may still expire.
                logger.exception("Lease heartbeat failed task_id=%s", self.claim.task_id)


class PdfWorker:
    def __init__(self, settings: Settings, factory, consumer: TaskConsumer,
                 processor: TaskProcessor, worker_id: str | None = None):
        self.settings = settings
        self.factory = factory
        self.consumer = consumer
        self.processor = processor
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4()}"

    def handle_message(self, value: bytes | None, *, now: datetime | None = None) -> dict:
        try:
            event = ParseEvent.model_validate_json(value or b"")
        except (ValidationError, json.JSONDecodeError, UnicodeDecodeError):
            # Poison message: record it and let the loop commit rather than crash-loop forever.
            logger.warning("Discarding malformed parse event")
            return {"outcome": "invalid_event"}

        with self.factory() as session, session.begin():
            claimed = claim_task(session, task_id=str(event.task_id),
                                 document_id=str(event.document_id), version=event.version,
                                 worker_id=self.worker_id, settings=self.settings, now=now)
        if claimed.outcome != "claimed" or claimed.claim is None:
            # Duplicate delivery, a newer manual version, an active lease or a future retry:
            # all are safe to acknowledge without touching a result.
            logger.info("Skipped task event task_id=%s version=%s reason=%s",
                        event.task_id, event.version, claimed.outcome)
            return {"outcome": claimed.outcome}
        claim = claimed.claim

        result: TaskResult | None = None
        error: TaskProcessingError | None = None
        with LeaseHeartbeat(self.settings, self.factory, claim) as lease:
            try:
                result = self.processor.process(claim)
            except TaskProcessingError as exc:
                error = exc
            except Exception as exc:
                error = TaskProcessingError("TASK_ERROR", f"解析任务异常: {type(exc).__name__}")
            if lease.lost and error is None:
                # Never persist a result under a lease another worker may already own.
                error = TaskProcessingError("LEASE_LOST", "任务租约已丢失，结果未提交", retryable=True)
                result = None

        with self.factory() as session, session.begin():
            if result is not None:
                saved = complete_task(session, claim, artifact_key=result.artifact_key,
                                      index_name=result.index_name, indexed_at=result.indexed_at,
                                      retrieval_mode=result.retrieval_mode)
                if not saved:
                    logger.warning("Discarded stale result task_id=%s version=%s",
                                   claim.task_id, claim.version)
                    return {"outcome": "stale"}
                return {"outcome": "completed"}

            assert error is not None
            if error.retryable and claim.attempts <= claim.max_retries:
                scheduled = schedule_retry(session, claim, settings=self.settings,
                                           error_code=error.code, error_message=error.message,
                                           error_details=error.details)
                return {"outcome": "retry_scheduled" if scheduled else "stale"}
            saved = fail_task(session, claim, settings=self.settings, error_code=error.code,
                              error_message=error.message, error_details=error.details)
            return {"outcome": "failed" if saved else "stale"}

    def run_forever(self, stop: threading.Event | None = None, poll_seconds: float | None = None) -> None:
        stop = stop or threading.Event()
        timeout = poll_seconds if poll_seconds is not None else self.settings.kafka_consumer_poll_seconds
        while not stop.is_set():
            try:
                record = self.consumer.poll(timeout)
            except Exception:
                logger.exception("Kafka poll failed; retrying")
                stop.wait(self.settings.outbox_poll_seconds)
                continue
            if record is None:
                continue
            try:
                outcome = self.handle_message(record.value)
            except Exception:
                # MySQL result persistence failed: leave the offset uncommitted and retry
                # the same record in this process instead of advancing past it.
                logger.exception("Task result persistence failed; rewinding record")
                try:
                    self.consumer.rewind(record)
                except Exception:
                    logger.exception("Consumer rewind failed")
                stop.wait(self.settings.task_retry_base_seconds)
                continue
            logger.info("Task event handled outcome=%s", outcome.get("outcome"))
            try:
                self.consumer.commit()
            except Exception:
                logger.exception("Kafka offset commit failed; rewinding record")
                try:
                    self.consumer.rewind(record)
                except Exception:
                    logger.exception("Consumer rewind failed")
                stop.wait(self.settings.task_retry_base_seconds)
        self.consumer.close()


def main() -> int:
    settings = get_settings()
    configure_logging(settings)
    engine = create_db_engine(settings)
    stop = threading.Event()

    def request_stop(*_):
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)
    from app.services.pdf_processing import MineruTaskProcessor

    consumer = None
    try:
        while consumer is None and not stop.is_set():
            try:
                consumer = create_consumer(settings)
            except Exception:
                logger.exception("Kafka unavailable; worker retrying")
                stop.wait(settings.task_retry_base_seconds)
        if consumer is not None:
            PdfWorker(settings, session_factory(engine), consumer,
                      MineruTaskProcessor(settings)).run_forever(stop)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
