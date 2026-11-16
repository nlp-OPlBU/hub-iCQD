"""ORM 映射：与 backend/sql/*.sql 的 DDL 一一对应。

DDL 是唯一真源，这里的模型只做读写映射，不负责建表。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "document"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=sql_text("'pending'"))
    stage: Mapped[str | None] = mapped_column(String(24))
    progress: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    image_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    md_path: Mapped[str | None] = mapped_column(String(512))
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP(3)"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=sql_text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=sql_text("CURRENT_TIMESTAMP(3)"),
    )


class Chunk(Base):
    __tablename__ = "chunk"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(32), nullable=False)
    idx: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    page_no: Mapped[int | None] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    image_path: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP(3)"))


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    title: Mapped[str | None] = mapped_column(String(64))
    summary: Mapped[str | None] = mapped_column(Text)
    summary_upto: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    message_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP(3)"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=sql_text("CURRENT_TIMESTAMP(3)"),
        server_onupdate=sql_text("CURRENT_TIMESTAMP(3)"),
    )


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    role: Mapped[str] = mapped_column(String(12), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=sql_text("CURRENT_TIMESTAMP(3)"))


class TraceSpan(Base):
    __tablename__ = "trace_span"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    span: Mapped[str] = mapped_column(String(48), nullable=False)
    parent: Mapped[str | None] = mapped_column(String(48))
    ref_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    start_ts: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dur_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    from_cache: Mapped[int] = mapped_column(Integer, nullable=False, server_default=sql_text("0"))
    attrs: Mapped[dict[str, Any] | None] = mapped_column(JSON)
