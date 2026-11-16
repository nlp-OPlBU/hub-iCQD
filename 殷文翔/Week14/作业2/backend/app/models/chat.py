from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.dialects.mysql import MEDIUMTEXT

from app.db.base import Base, Timestamps, new_id


class Conversation(Timestamps, Base):
    __tablename__ = "conversations"
    __table_args__ = (Index("ix_conversations_created_id", "created_at", "id"),
                      {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"})
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    title: Mapped[str] = mapped_column(String(200), default="新会话")
    next_sequence: Mapped[int] = mapped_column(Integer, default=1)


class Message(Timestamps, Base):
    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_message_sequence"),
        UniqueConstraint("conversation_id", "request_key", "role", name="uq_message_request_role"),
        CheckConstraint("sequence >= 1", name="positive_sequence"),
        CheckConstraint("role IN ('user','assistant')", name="role"),
        CheckConstraint("status IN ('pending','completed','failed')", name="status"),
        {"mysql_charset": "utf8mb4", "mysql_engine": "InnoDB"},
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"))
    sequence: Mapped[int] = mapped_column(Integer)
    request_key: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    content: Mapped[str] = mapped_column(Text().with_variant(MEDIUMTEXT(), "mysql"), default="")
    image_ids: Mapped[list] = mapped_column(JSON, default=list)
    document_scope: Mapped[list | None] = mapped_column(JSON)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    model: Mapped[str | None] = mapped_column(String(255))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(String(512))
