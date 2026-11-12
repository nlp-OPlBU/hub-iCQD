"""接口出入参模型。"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer

from .models import DocumentStatus


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    original_name: str
    file_size: int
    status: DocumentStatus
    error_message: str | None = None
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime

    @field_serializer("created_at", "updated_at")
    def _serialize_datetime(self, value: datetime) -> str:
        # SQLite 读出来可能是 naive，统一按 UTC 输出，前端 new Date() 才能正确解析
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()


class UploadResponse(BaseModel):
    documents: list[DocumentOut]


class RetryResponse(BaseModel):
    document: DocumentOut
    message: str = ""


class DeleteResponse(BaseModel):
    ok: bool = True
    message: str = ""


class SourceItem(BaseModel):
    doc_id: int
    file_name: str
    snippet: str
    score: float
    image_urls: list[str] = []


class ChatResponse(BaseModel):
    answer: str
    sources: list[SourceItem] = []
    images: list[str] = []
