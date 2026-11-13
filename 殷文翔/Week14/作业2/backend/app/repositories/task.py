"""Parse-task state transitions guarded by status, lease token and latest version."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal
from uuid import uuid4

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import utcnow
from app.models import Document, ParseTask
from app.repositories.outbox import add_parse_failed, requeue_parse_requested


@dataclass(frozen=True)
class TaskClaim:
    task_id: str
    document_id: str
    version: int
    storage_key: str
    attempts: int
    max_retries: int
    lease_token: str
    retrieval_mode: str
    original_filename: str = ""


@dataclass(frozen=True)
class ClaimResult:
    outcome: Literal["claimed", "missing", "mismatch", "stale", "completed", "failed", "busy", "not_due"]
    claim: TaskClaim | None = None


def _latest_version_exists(document_id: str, version: int):
    return exists(select(Document.id).where(Document.id == document_id,
                                           Document.latest_version == version))


def claim_task(session: Session, *, task_id: str, document_id: str, version: int,
               worker_id: str, settings: Settings, now: datetime | None = None) -> ClaimResult:
    now = now or utcnow()
    task = session.scalar(select(ParseTask).where(ParseTask.id == task_id).with_for_update())
    if task is None:
        return ClaimResult("missing")
    document = session.scalar(select(Document).where(Document.id == task.document_id).with_for_update())
    if document is None or task.document_id != document_id or task.version != version:
        return ClaimResult("mismatch")
    if document.latest_version != task.version:
        return ClaimResult("stale")
    if task.status == "completed":
        return ClaimResult("completed")
    if task.status == "failed":
        return ClaimResult("failed")
    if task.status == "processing" and task.lease_expires_at and task.lease_expires_at > now:
        return ClaimResult("busy")
    if task.status == "pending" and task.next_attempt_at and task.next_attempt_at > now:
        return ClaimResult("not_due")
    if task.status not in ("pending", "processing"):
        return ClaimResult("failed")
    # A redelivery can arrive before the dispatcher recovers the expired lease.
    # Enforce the same budget on this path, otherwise repeated crashes bypass it.
    if task.attempts >= task.max_retries + 1:
        task.status, task.stage = "failed", "failed"
        task.error_code = "WORKER_LOST"
        task.error_message = "Worker 租约过期且重试预算已耗尽"
        task.finished_at = now
        task.next_attempt_at = None
        task.lease_owner = task.lease_token = task.lease_expires_at = task.heartbeat_at = None
        add_parse_failed(session, settings, task_id=task.id, document_id=document.id,
                         version=task.version, storage_key=document.storage_key)
        session.flush()
        return ClaimResult("failed")

    token = str(uuid4())
    task.status = "processing"
    task.stage = "parsing"
    task.attempts += 1
    task.lease_owner = worker_id
    task.lease_token = token
    task.lease_expires_at = now + timedelta(seconds=settings.task_lease_seconds)
    task.heartbeat_at = now
    task.error_code = None
    task.error_message = None
    task.error_details = None
    task.next_attempt_at = None
    task.finished_at = None
    if task.started_at is None:
        task.started_at = now
    session.flush()
    return ClaimResult("claimed", TaskClaim(
        task_id=task.id, document_id=document.id, version=task.version,
        storage_key=document.storage_key, attempts=task.attempts,
        max_retries=task.max_retries, lease_token=token,
        retrieval_mode=task.retrieval_mode, original_filename=document.original_filename,
    ))


def heartbeat(session: Session, claim: TaskClaim, *, settings: Settings,
              now: datetime | None = None) -> bool:
    now = now or utcnow()
    result = session.execute(update(ParseTask).where(
        ParseTask.id == claim.task_id, ParseTask.document_id == claim.document_id,
        ParseTask.version == claim.version, ParseTask.status == "processing",
        ParseTask.lease_token == claim.lease_token,
        _latest_version_exists(claim.document_id, claim.version),
    ).values(heartbeat_at=now, lease_expires_at=now + timedelta(seconds=settings.task_lease_seconds)))
    return result.rowcount == 1


def complete_task(session: Session, claim: TaskClaim, *, artifact_key: str, index_name: str,
                  indexed_at: datetime, retrieval_mode: str, now: datetime | None = None) -> bool:
    now = now or utcnow()
    result = session.execute(update(ParseTask).where(
        ParseTask.id == claim.task_id, ParseTask.document_id == claim.document_id,
        ParseTask.version == claim.version, ParseTask.status == "processing",
        ParseTask.lease_token == claim.lease_token,
        _latest_version_exists(claim.document_id, claim.version),
    ).values(status="completed", stage="done", artifact_key=artifact_key,
             index_name=index_name, indexed_at=indexed_at, retrieval_mode=retrieval_mode,
             lease_owner=None, lease_token=None, lease_expires_at=None,
             heartbeat_at=None, finished_at=now, error_code=None, error_message=None,
             error_details=None, next_attempt_at=None))
    if result.rowcount != 1:
        return False
    session.execute(update(Document).where(
        Document.id == claim.document_id, Document.latest_version == claim.version,
    ).values(current_searchable_version=claim.version))
    session.flush()
    return True


def schedule_retry(session: Session, claim: TaskClaim, *, settings: Settings,
                   error_code: str, error_message: str, error_details: dict | None,
                   now: datetime | None = None) -> bool:
    """Return True when a retry was scheduled; False means stale or no longer owned."""
    now = now or utcnow()
    next_attempt = now + timedelta(seconds=min(
        settings.task_retry_base_seconds * 2 ** max(claim.attempts - 1, 0),
        settings.task_retry_max_seconds,
    ))
    result = session.execute(update(ParseTask).where(
        ParseTask.id == claim.task_id, ParseTask.document_id == claim.document_id,
        ParseTask.version == claim.version, ParseTask.status == "processing",
        ParseTask.lease_token == claim.lease_token,
        _latest_version_exists(claim.document_id, claim.version),
    ).values(status="pending", stage="queued", error_code=error_code,
             error_message=error_message[:512], error_details=error_details or {},
             next_attempt_at=next_attempt, lease_owner=None, lease_token=None,
             lease_expires_at=None, heartbeat_at=None, finished_at=None))
    if result.rowcount != 1:
        return False
    requeue_parse_requested(session, task_id=claim.task_id, document_id=claim.document_id,
                            version=claim.version, available_at=next_attempt)
    session.flush()
    return True


def fail_task(session: Session, claim: TaskClaim, *, settings: Settings, error_code: str,
              error_message: str, error_details: dict | None, now: datetime | None = None) -> bool:
    now = now or utcnow()
    result = session.execute(update(ParseTask).where(
        ParseTask.id == claim.task_id, ParseTask.document_id == claim.document_id,
        ParseTask.version == claim.version, ParseTask.status == "processing",
        ParseTask.lease_token == claim.lease_token,
        _latest_version_exists(claim.document_id, claim.version),
    ).values(status="failed", stage="failed", error_code=error_code,
             error_message=error_message[:512], error_details=error_details or {},
             lease_owner=None, lease_token=None, lease_expires_at=None,
             heartbeat_at=None, finished_at=now, next_attempt_at=None))
    if result.rowcount != 1:
        return False
    storage_key = session.scalar(select(Document.storage_key).where(Document.id == claim.document_id))
    add_parse_failed(session, settings, task_id=claim.task_id, document_id=claim.document_id,
                     version=claim.version, storage_key=storage_key or "")
    session.flush()
    return True


def fail_pending_task(session: Session, *, task_id: str, document_id: str, version: int,
                      settings: Settings, error_code: str, error_message: str,
                      error_details: dict | None = None, now: datetime | None = None) -> bool:
    """Used when the outbox exhausts its publish budget before Kafka ever accepts the event."""
    now = now or utcnow()
    result = session.execute(update(ParseTask).where(
        ParseTask.id == task_id, ParseTask.document_id == document_id,
        ParseTask.version == version, ParseTask.status == "pending",
        _latest_version_exists(document_id, version),
    ).values(status="failed", stage="failed", error_code=error_code,
             error_message=error_message[:512], error_details=error_details or {},
             lease_owner=None, lease_token=None, lease_expires_at=None,
             heartbeat_at=None, finished_at=now, next_attempt_at=None))
    if result.rowcount != 1:
        return False
    storage_key = session.scalar(select(Document.storage_key).where(Document.id == document_id))
    add_parse_failed(session, settings, task_id=task_id, document_id=document_id,
                     version=version, storage_key=storage_key or "")
    session.flush()
    return True


def recover_expired_tasks(session: Session, settings: Settings, *, now: datetime | None = None,
                          limit: int = 50) -> list[str]:
    """Return expired processing leases to pending so a crashed worker cannot strand them."""
    now = now or utcnow()
    tasks = session.scalars(select(ParseTask).where(
        ParseTask.status == "processing",
        (ParseTask.lease_expires_at.is_(None)) | (ParseTask.lease_expires_at <= now),
    ).order_by(ParseTask.lease_expires_at, ParseTask.id).limit(limit)
      .with_for_update(skip_locked=True)).all()
    recovered = []
    for task in tasks:
        document = session.scalar(select(Document).where(Document.id == task.document_id)
                                  .with_for_update())
        if document is None:
            continue
        if document.latest_version != task.version:
            # An old worker must never revive or overwrite the newer version.
            task.status, task.stage = "failed", "failed"
            task.error_code = "STALE_VERSION"
            task.error_message = "任务版本已被新的人工重试版本取代"
            task.finished_at = now
            task.lease_owner = task.lease_token = task.lease_expires_at = task.heartbeat_at = None
            session.flush()
            continue
        if task.attempts >= task.max_retries + 1:
            task.status, task.stage = "failed", "failed"
            task.error_code = "WORKER_LOST"
            task.error_message = "Worker 租约过期且重试预算已耗尽"
            task.finished_at = now
            task.lease_owner = task.lease_token = task.lease_expires_at = task.heartbeat_at = None
            add_parse_failed(session, settings, task_id=task.id, document_id=task.document_id,
                             version=task.version, storage_key=document.storage_key)
        else:
            task.status, task.stage = "pending", "queued"
            task.error_code = "WORKER_LOST"
            task.error_message = "Worker 租约过期，任务已恢复等待重试"
            task.next_attempt_at = now
            task.finished_at = None
            task.lease_owner = task.lease_token = task.lease_expires_at = task.heartbeat_at = None
            requeue_parse_requested(session, task_id=task.id, document_id=task.document_id,
                                    version=task.version, available_at=now)
        recovered.append(task.id)
        session.flush()
    return recovered
