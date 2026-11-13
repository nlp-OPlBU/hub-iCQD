from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UTCDateTime, new_id, utcnow


class OutboxEvent(Timestamps, Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        ForeignKeyConstraint(["task_id", "document_id", "version"], ["parse_tasks.id", "parse_tasks.document_id", "parse_tasks.version"], name="fk_outbox_task_identity"),
        UniqueConstraint("task_id", "event_type", name="uq_outbox_task_event"),
        CheckConstraint("status IN ('pending','publishing','published','failed')", name="status"),
        CheckConstraint("attempts >= 0 AND max_retries >= 0 AND version >= 1", name="counters"),
        Index("ix_outbox_dispatch", "status", "available_at"),
        Index("ix_outbox_lease", "status", "lease_expires_at"),
        {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"},
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    task_id: Mapped[str] = mapped_column(String(36))
    document_id: Mapped[str] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(64))
    topic: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer)
    available_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
