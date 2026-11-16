from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, Timestamps, UTCDateTime, new_id


class Document(Timestamps, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("latest_version >= 1", name="positive_version"),
        CheckConstraint("current_searchable_version IS NULL OR (current_searchable_version >= 1 AND current_searchable_version <= latest_version)", name="searchable_version"),
        CheckConstraint("size_bytes > 0", name="positive_size"),
        ForeignKeyConstraint(["id", "current_searchable_version"], ["parse_tasks.document_id", "parse_tasks.version"],
                             name="fk_document_searchable_task", use_alter=True),
        Index("ix_documents_created_id", "created_at", "id"),
        {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"},
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    original_filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    media_type: Mapped[str] = mapped_column(String(100), default="application/pdf")
    latest_version: Mapped[int] = mapped_column(Integer, default=1)
    current_searchable_version: Mapped[int | None] = mapped_column(Integer)


class ParseTask(Timestamps, Base):
    __tablename__ = "parse_tasks"
    __table_args__ = (
        UniqueConstraint("document_id", "version", name="uq_task_document_version"),
        UniqueConstraint("id", "document_id", "version", name="uq_task_identity"),
        UniqueConstraint("document_id", "retry_key", name="uq_task_retry_key"),
        CheckConstraint("version >= 1 AND attempts >= 0 AND max_retries >= 0", name="counters"),
        CheckConstraint("status IN ('pending','processing','completed','failed')", name="status"),
        Index("ix_tasks_status_next", "status", "next_attempt_at"),
        Index("ix_tasks_lease", "status", "lease_expires_at"),
        {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"},
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    document_id: Mapped[str] = mapped_column(ForeignKey("documents.id"))
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer)
    retry_key: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
    error_details: Mapped[dict | None] = mapped_column(JSON)
    next_attempt_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    artifact_key: Mapped[str | None] = mapped_column(String(512))
    index_name: Mapped[str | None] = mapped_column(String(255))
    indexed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    retrieval_mode: Mapped[str] = mapped_column(String(16), default="bm25")


class ImageResource(Timestamps, Base):
    __tablename__ = "image_resources"
    __table_args__ = (
        ForeignKeyConstraint(["document_id", "version"], ["parse_tasks.document_id", "parse_tasks.version"], name="fk_image_task"),
        CheckConstraint("kind IN ('user_upload','document_image')", name="kind"),
        CheckConstraint("(kind = 'user_upload' AND conversation_id IS NOT NULL AND document_id IS NULL AND version IS NULL AND page IS NULL) OR (kind = 'document_image' AND document_id IS NOT NULL AND version IS NOT NULL AND page >= 1 AND page IS NOT NULL AND conversation_id IS NULL)", name="ownership"),
        CheckConstraint("size_bytes > 0 AND width > 0 AND height > 0", name="dimensions"),
        {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"},
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(32))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    media_type: Mapped[str] = mapped_column(String(100))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    document_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int | None] = mapped_column(Integer)
    page: Mapped[int | None] = mapped_column(Integer)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"))
