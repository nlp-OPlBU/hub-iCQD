"""Outbox persistence. Inserts join the caller's transaction; publishing is done by the dispatcher."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.models import OutboxEvent
from app.schemas.document import ParseEvent

PARSE_REQUESTED = "pdf.parse.requested"
PARSE_FAILED = "pdf.parse.failed"


def add_parse_requested(session: Session, settings: Settings, event: ParseEvent) -> None:
    session.add(OutboxEvent(
        id=str(event.event_id), task_id=str(event.task_id), document_id=str(event.document_id),
        version=event.version, event_type=PARSE_REQUESTED, topic=settings.kafka_pdf_topic,
        payload=event.model_dump(mode="json"), status="pending", attempts=0,
        max_retries=settings.outbox_max_retries,
    ))


def add_parse_failed(session: Session, settings: Settings, *, task_id: str, document_id: str,
                     version: int, storage_key: str) -> OutboxEvent:
    """Insert the failure-topic event once per task, inside the failure transaction."""
    existing = session.scalar(select(OutboxEvent).where(
        OutboxEvent.task_id == task_id, OutboxEvent.event_type == PARSE_FAILED))
    if existing is not None:
        return existing
    payload = {
        "schema_version": 1,
        "event_id": str(uuid4()),
        "document_id": document_id,
        "task_id": task_id,
        "version": version,
        "storage_key": storage_key,
    }
    event = OutboxEvent(
        id=payload["event_id"], task_id=task_id, document_id=document_id, version=version,
        event_type=PARSE_FAILED, topic=settings.kafka_failed_topic, payload=payload,
        status="pending", attempts=0, max_retries=settings.outbox_max_retries,
    )
    session.add(event)
    return event


def requeue_parse_requested(session: Session, *, task_id: str, document_id: str, version: int,
                            available_at: datetime, clear_publish_attempts: bool = True) -> bool:
    """Reuse the single parse-requested event; never insert a second event for a retry."""
    values = {
        "status": "pending", "available_at": available_at, "lease_owner": None,
        "lease_token": None, "lease_expires_at": None, "published_at": None,
        "error_code": None, "error_message": None,
    }
    if clear_publish_attempts:
        values["attempts"] = 0
    result = session.execute(update(OutboxEvent).where(
        OutboxEvent.task_id == task_id, OutboxEvent.document_id == document_id,
        OutboxEvent.version == version, OutboxEvent.event_type == PARSE_REQUESTED,
    ).values(**values))
    return result.rowcount == 1


@dataclass(frozen=True)
class OutboxClaim:
    id: str
    task_id: str
    document_id: str
    version: int
    event_type: str
    topic: str
    payload: dict
    attempts: int
    max_retries: int
    lease_token: str


def due_condition(now: datetime):
    return or_(
        and_(OutboxEvent.status == "pending", OutboxEvent.available_at <= now),
        and_(OutboxEvent.status == "publishing",
             or_(OutboxEvent.lease_expires_at.is_(None), OutboxEvent.lease_expires_at <= now)),
    )


def claim_outbox(session: Session, settings: Settings, *, owner: str, now: datetime | None = None,
                 limit: int | None = None) -> list[OutboxClaim]:
    now = now or utcnow()
    query = (select(OutboxEvent).where(due_condition(now),
                                       OutboxEvent.attempts < OutboxEvent.max_retries)
             .order_by(OutboxEvent.available_at, OutboxEvent.created_at, OutboxEvent.id)
             .limit(limit or settings.outbox_batch_size))
    # MySQL 8 supports SKIP LOCKED so multiple dispatchers can share the queue.
    query = query.with_for_update(skip_locked=True)
    records = session.scalars(query).all()
    claims = []
    for record in records:
        token = str(uuid4())
        record.status = "publishing"
        record.attempts += 1
        record.lease_owner = owner
        record.lease_token = token
        record.lease_expires_at = now + _seconds(settings.outbox_lease_seconds)
        record.error_code = None
        record.error_message = None
        session.flush()
        claims.append(OutboxClaim(
            id=record.id, task_id=record.task_id, document_id=record.document_id,
            version=record.version, event_type=record.event_type, topic=record.topic,
            payload=record.payload, attempts=record.attempts, max_retries=record.max_retries,
            lease_token=token,
        ))
    return claims


def mark_published(session: Session, claim: OutboxClaim, *, now: datetime | None = None) -> bool:
    result = session.execute(update(OutboxEvent).where(
        OutboxEvent.id == claim.id, OutboxEvent.status == "publishing",
        OutboxEvent.lease_token == claim.lease_token,
    ).values(status="published", published_at=now or utcnow(), lease_owner=None, lease_token=None,
             lease_expires_at=None, error_code=None, error_message=None))
    return result.rowcount == 1


def mark_publish_failed(session: Session, claim: OutboxClaim, *, settings: Settings,
                        error_code: str, error_message: str,
                        now: datetime | None = None) -> bool:
    """Return True when the event is terminal (retry budget exhausted)."""
    now = now or utcnow()
    terminal = claim.attempts >= claim.max_retries
    values = {
        "status": "failed" if terminal else "pending",
        "available_at": now + _seconds(backoff_seconds(claim.attempts, settings)),
        "lease_owner": None, "lease_token": None, "lease_expires_at": None,
        "error_code": error_code, "error_message": error_message[:512],
    }
    result = session.execute(update(OutboxEvent).where(
        OutboxEvent.id == claim.id, OutboxEvent.status == "publishing",
        OutboxEvent.lease_token == claim.lease_token,
    ).values(**values))
    return terminal and result.rowcount == 1


def exhausted_events(session: Session, *, now: datetime | None = None) -> list[OutboxEvent]:
    """Events whose lease expired or delayed retry is due but whose budget is gone."""
    now = now or utcnow()
    return session.scalars(select(OutboxEvent).where(
        OutboxEvent.attempts >= OutboxEvent.max_retries, due_condition(now),
    ).with_for_update(skip_locked=True)).all()


def mark_exhausted(session: Session, event: OutboxEvent, *, now: datetime | None = None) -> None:
    event.status = "failed"
    event.available_at = now or utcnow()
    event.lease_owner = None
    event.lease_token = None
    event.lease_expires_at = None
    event.error_code = event.error_code or "OUTBOX_RETRY_EXHAUSTED"
    event.error_message = event.error_message or "Kafka delivery retry budget exhausted"
    session.flush()


def backoff_seconds(attempts: int, settings: Settings) -> int:
    return min(settings.task_retry_base_seconds * 2 ** max(attempts - 1, 0),
               settings.task_retry_max_seconds)


def _seconds(value: int):
    return timedelta(seconds=value)
