"""Transactional outbox dispatcher: MySQL is the source, Kafka only receives copies."""
from __future__ import annotations

import logging
import os
import signal
import socket
import threading
from datetime import datetime
from uuid import uuid4

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_db_engine, session_factory
from app.repositories.outbox import (PARSE_REQUESTED, claim_outbox, exhausted_events,
                                     mark_exhausted, mark_publish_failed, mark_published)
from app.repositories.task import fail_pending_task, recover_expired_tasks
from app.services.kafka_producer import EventPublisher, PublishError, create_publisher

logger = logging.getLogger(__name__)


class OutboxDispatcher:
    def __init__(self, settings: Settings, factory, publisher: EventPublisher,
                 owner: str | None = None):
        self.settings = settings
        self.factory = factory
        self.publisher = publisher
        self.owner = owner or f"{socket.gethostname()}:{os.getpid()}:{uuid4()}"

    def run_once(self, *, now: datetime | None = None) -> dict[str, int]:
        stats = {"exhausted": 0, "recovered": 0, "claimed": 0,
                 "published": 0, "requeued": 0, "failed": 0}
        # 1. Terminal outbox rows must fail the still-pending task instead of silently dropping it.
        with self.factory() as session, session.begin():
            for event in exhausted_events(session, now=now):
                mark_exhausted(session, event, now=now)
                stats["exhausted"] += 1
                if event.event_type == PARSE_REQUESTED:
                    if fail_pending_task(session, task_id=event.task_id,
                                         document_id=event.document_id, version=event.version,
                                         settings=self.settings, error_code="OUTBOX_EXHAUSTED",
                                         error_message="Kafka 投递重试预算已耗尽", now=now):
                        stats["failed"] += 1

        # 2. A crashed worker's lease is returned to pending and its unchanged event is reused.
        with self.factory() as session, session.begin():
            stats["recovered"] = len(recover_expired_tasks(session, self.settings, now=now))

        # 3. Claim with a persistent lease, then send outside the transaction.
        with self.factory() as session, session.begin():
            claims = claim_outbox(session, self.settings, owner=self.owner, now=now)
        stats["claimed"] = len(claims)

        for claim in claims:
            try:
                confirmed = self.publisher.publish(claim.topic, claim.task_id, claim.payload)
                if confirmed is False:
                    raise PublishError("Kafka did not confirm the event")
            except PublishError as exc:
                logger.warning("Outbox publish failed event_id=%s topic=%s attempts=%s",
                               claim.id, claim.topic, claim.attempts)
                with self.factory() as session, session.begin():
                    terminal = mark_publish_failed(
                        session, claim, settings=self.settings,
                        error_code="KAFKA_UNAVAILABLE", error_message=str(exc), now=now)
                    if terminal:
                        stats["failed"] += 1
                        if claim.event_type == PARSE_REQUESTED:
                            fail_pending_task(session, task_id=claim.task_id,
                                              document_id=claim.document_id, version=claim.version,
                                              settings=self.settings, error_code="OUTBOX_EXHAUSTED",
                                              error_message="Kafka 投递重试预算已耗尽", now=now)
                    else:
                        stats["requeued"] += 1
            except Exception:
                # Unknown client error: keep the lease to expire naturally; never mark published.
                logger.exception("Unexpected outbox publisher error event_id=%s", claim.id)
                stats["requeued"] += 1
            else:
                with self.factory() as session, session.begin():
                    if mark_published(session, claim, now=now):
                        stats["published"] += 1
                    else:
                        # Another dispatcher recovered the expired lease; re-send is harmless.
                        stats["requeued"] += 1
        return stats

    def run_forever(self, stop: threading.Event | None = None) -> None:
        stop = stop or threading.Event()
        while not stop.is_set():
            try:
                stats = self.run_once()
                if any(stats.values()):
                    logger.info("Outbox cycle %s", stats)
            except Exception:
                # MySQL/transient failures: keep the process alive and retry next cycle.
                logger.exception("Outbox cycle failed")
            stop.wait(self.settings.outbox_poll_seconds)
        self.publisher.close()


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
    dispatcher = OutboxDispatcher(settings, session_factory(engine), create_publisher(settings))
    try:
        dispatcher.run_forever(stop)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
