from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from app.schemas.chat import ImageRead
from app.schemas.common import Contract, ErrorDetail, UTCTimestamp

TaskStatus = Literal["pending", "processing", "completed", "failed"]
TaskStage = Literal["queued", "parsing", "persisting", "indexing", "done", "failed"]


class TaskRead(Contract):
    id: UUID
    document_id: UUID
    version: int = Field(ge=1)
    status: TaskStatus
    stage: TaskStage
    attempts: int = Field(ge=0)
    max_retries: int = Field(ge=0)
    error: ErrorDetail | None = None
    next_attempt_at: UTCTimestamp | None = None
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


class DocumentRead(Contract):
    id: UUID
    original_filename: str
    size_bytes: int = Field(gt=0)
    latest_version: int = Field(ge=1)
    current_searchable_version: int | None = Field(default=None, ge=1)
    latest_task: TaskRead
    created_at: UTCTimestamp
    updated_at: UTCTimestamp


class UploadAccepted(Contract):
    index: int = Field(ge=0)
    filename: str
    outcome: Literal["accepted"] = "accepted"
    status_code: Literal[202] = 202
    document: DocumentRead


class UploadRejected(Contract):
    index: int = Field(ge=0)
    filename: str
    outcome: Literal["rejected"] = "rejected"
    status_code: Literal[413, 415, 422, 503]
    error: ErrorDetail


class BatchUploadResponse(Contract):
    request_id: str
    accepted: int = Field(ge=0)
    rejected: int = Field(ge=0)
    results: list[Annotated[UploadAccepted | UploadRejected, Field(discriminator="outcome")]]

    @model_validator(mode="after")
    def coherent_results(self):
        if self.accepted != sum(r.outcome == "accepted" for r in self.results) or self.rejected != sum(r.outcome == "rejected" for r in self.results):
            raise ValueError("Batch counts must match results")
        if [r.index for r in self.results] != list(range(len(self.results))):
            raise ValueError("Results must preserve every input position")
        return self


class RetryRequest(Contract):
    expected_latest_version: int = Field(ge=1)


class ParseEvent(Contract):
    schema_version: Literal[1] = 1
    event_id: UUID
    document_id: UUID
    task_id: UUID
    version: int = Field(ge=1)
    storage_key: str = Field(min_length=1, max_length=512)


class EvidenceBlockRead(Contract):
    block_index: int = Field(ge=0)
    evidence_type: Literal['text', 'table', 'image']
    text_origin: Literal['content', 'page_context'] = 'content'
    text: str
    bbox: list[float] | None = None
    images: list[ImageRead] = Field(default_factory=list)


class DocumentPageRead(Contract):
    document_id: UUID
    version: int = Field(ge=1)
    filename: str
    page: int = Field(ge=1)
    page_count: int = Field(ge=1)
    blocks: list[EvidenceBlockRead]
