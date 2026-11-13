from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.common import Contract, ErrorDetail, UTCTimestamp


class ConversationCreate(Contract):
    title: str = Field(default="新会话", min_length=1, max_length=200)


class ConversationRead(ConversationCreate):
    id: UUID
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


class ImageRead(Contract):
    id: UUID
    kind: Literal["user_upload", "document_image"]
    url: str = Field(pattern=r"^/api/images/[0-9a-fA-F-]{36}/content$")
    media_type: Literal["image/png", "image/jpeg", "image/webp"]
    size_bytes: int = Field(gt=0)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    document_id: UUID | None = None
    version: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)


class SourceReference(Contract):
    source_id: str = Field(pattern=r"^S[1-9][0-9]*$")
    document_id: UUID
    version: int = Field(ge=1)
    filename: str
    page: int | None = Field(default=None, ge=1)
    chunk_id: str
    image_ids: list[UUID] = Field(default_factory=list)
    images: list[ImageRead] = Field(default_factory=list)


class QuestionRequest(Contract):
    text: str = Field(default="", max_length=20000)
    image_ids: list[UUID] = Field(default_factory=list, max_length=32)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def nonempty(self):
        self.text = self.text.strip()
        if not self.text and not self.image_ids:
            raise ValueError("A question requires text or images")
        if self.document_ids is not None and not self.document_ids:
            raise ValueError("document_ids must be null or a nonempty explicit scope")
        if len(set(self.image_ids)) != len(self.image_ids):
            raise ValueError("Duplicate image IDs are not allowed")
        if self.document_ids is not None and len(set(self.document_ids)) != len(self.document_ids):
            raise ValueError("Duplicate document IDs are not allowed")
        return self


class SearchRequest(Contract):
    text: str = Field(min_length=1, max_length=20000)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)
    limit: int = Field(default=6, ge=1, le=30)

    @model_validator(mode="after")
    def validate_scope(self):
        self.text = self.text.strip()
        if not self.text or self.document_ids == []:
            raise ValueError("Text and explicit document scope must be nonempty")
        return self


class SearchHit(Contract):
    text: str
    score: float
    source: SourceReference


class MessageRead(Contract):
    id: UUID
    conversation_id: UUID
    sequence: int = Field(ge=1)
    role: Literal["user", "assistant"]
    status: Literal["pending", "completed", "failed"]
    content: str
    images: list[ImageRead] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    error: ErrorDetail | None = None
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


class AnswerResponse(Contract):
    user_message: MessageRead
    assistant_message: MessageRead
