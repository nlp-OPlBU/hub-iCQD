"""文档元信息表。解析状态就存在这里，前端轮询接口读取。"""

import enum
from datetime import datetime, timezone

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"       # 待解析：已上传、消息已投递 Kafka
    PARSING = "parsing"       # 解析中：worker 正在跑 MinerU / 向量化
    COMPLETED = "completed"   # 解析完成：markdown、图片、向量均已落库
    FAILED = "failed"         # 解析失败


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_name: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    status: Mapped[DocumentStatus] = mapped_column(
        Enum(
            DocumentStatus,
            values_callable=lambda e: [m.value for m in e],
            native_enum=False,
            length=20,
        ),
        default=DocumentStatus.PENDING,
        index=True,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # MinerU 产物位置
    parsed_dir: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    markdown_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    def __repr__(self) -> str:  # 方便日志排查
        return f"<Document id={self.id} name={self.original_name!r} status={self.status.value}>"
