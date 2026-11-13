"""Document persistence and snapshot reads. Transaction boundaries belong to callers."""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.errors import ApiError
from app.core.config import Settings
from app.db.base import new_id
from app.models import Document, OutboxEvent, ParseTask
from app.repositories.outbox import add_parse_requested
from app.schemas.common import ErrorDetail, Page
from app.schemas.document import DocumentRead, ParseEvent, TaskRead


def task_read(task: ParseTask) -> TaskRead:
    error = None
    if task.error_code:
        error = ErrorDetail(code=task.error_code, message=task.error_message or "任务处理失败",
                            details=task.error_details or {})
    return TaskRead(
        id=task.id, document_id=task.document_id, version=task.version, status=task.status,
        stage=task.stage, attempts=task.attempts, max_retries=task.max_retries, error=error,
        next_attempt_at=task.next_attempt_at, created_at=task.created_at, updated_at=task.updated_at,
    )


def document_read(document: Document, task: ParseTask) -> DocumentRead:
    return DocumentRead(
        id=document.id, original_filename=document.original_filename, size_bytes=document.size_bytes,
        latest_version=document.latest_version, current_searchable_version=document.current_searchable_version,
        latest_task=task_read(task), created_at=document.created_at, updated_at=document.updated_at,
    )


class DocumentRepository:
    def __init__(self, session: Session):
        self.session = session

    def check_ready(self) -> None:
        for model in (Document, ParseTask, OutboxEvent):
            self.session.execute(select(model.id).limit(1))

    def add_upload(self, *, document_id: str, task_id: str, filename: str, storage_key: str,
                   size_bytes: int, sha256: str, settings: Settings) -> tuple[Document, ParseTask]:
        document = Document(id=document_id, original_filename=filename, storage_key=storage_key,
                            sha256=sha256, size_bytes=size_bytes, media_type="application/pdf",
                            latest_version=1)
        self.session.add(document)
        self.session.flush()  # Explicit ordering for the document/task/outbox foreign keys.
        task = ParseTask(id=task_id, document_id=document_id, version=1, status="pending",
                         stage="queued", attempts=0, max_retries=settings.task_max_retries,
                         retrieval_mode=settings.retrieval_mode)
        self.session.add(task)
        self.session.flush()
        return document, task

    @staticmethod
    def latest_query():
        return select(Document, ParseTask).join(ParseTask, (ParseTask.document_id == Document.id)
                                               & (ParseTask.version == Document.latest_version))

    def get(self, document_id: str) -> DocumentRead | None:
        row = self.session.execute(self.latest_query().where(Document.id == document_id)).first()
        return document_read(*row) if row else None

    def get_task(self, task_id: str) -> TaskRead | None:
        task = self.session.get(ParseTask, task_id)
        return task_read(task) if task else None

    def storage_key(self, document_id: str) -> str | None:
        return self.session.scalar(select(Document.storage_key).where(Document.id == document_id))

    def source_record(self, document_id: str, version: int | None = None):
        document = self.session.get(Document, document_id)
        if document is None:
            raise ApiError(404, 'DOCUMENT_NOT_FOUND', '文档不存在')
        selected = version if version is not None else (document.current_searchable_version or document.latest_version)
        task = self.session.scalar(select(ParseTask).where(
            ParseTask.document_id == document_id, ParseTask.version == selected))
        if task is None:
            raise ApiError(404, 'DOCUMENT_VERSION_NOT_FOUND', '文档版本不存在')
        return document, task

    def retry(self, document_id: str, expected_latest_version: int, retry_key: str,
              settings: Settings) -> TaskRead:
        """Create a new manual version; idempotency and latest-version checks share one lock."""
        document = self.session.scalar(select(Document).where(Document.id == document_id)
                                       .with_for_update())
        if document is None:
            raise ApiError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
        existing = self.session.scalar(select(ParseTask).where(
            ParseTask.document_id == document_id, ParseTask.retry_key == retry_key))
        if existing is not None:
            if existing.version == expected_latest_version + 1:
                return task_read(existing)
            raise ApiError(409, "IDEMPOTENCY_CONFLICT", "相同幂等键对应了不同的重试请求",
                           details={"latest_version": document.latest_version})
        if document.latest_version != expected_latest_version:
            raise ApiError(409, "VERSION_CONFLICT", "文档已有更新的解析版本，请刷新后重试",
                           details={"latest_version": document.latest_version})
        latest = self.session.scalar(select(ParseTask).where(
            ParseTask.document_id == document_id, ParseTask.version == expected_latest_version))
        if latest is None or latest.status != "failed":
            raise ApiError(409, "TASK_NOT_RETRYABLE", "只有失败的最新任务可以重试",
                           details={"status": latest.status if latest else None})
        task = ParseTask(
            id=new_id(), document_id=document_id, version=expected_latest_version + 1,
            status="pending", stage="queued", attempts=0, max_retries=settings.task_max_retries,
            retry_key=retry_key, retrieval_mode=settings.retrieval_mode,
        )
        self.session.add(task)
        document.latest_version = expected_latest_version + 1
        self.session.flush()
        add_parse_requested(self.session, settings, ParseEvent(
            event_id=new_id(), document_id=document_id, task_id=task.id,
            version=task.version, storage_key=document.storage_key))
        self.session.flush()
        return task_read(task)

    def list(self, page: int, page_size: int, status: str | None) -> Page[DocumentRead]:
        query = self.latest_query()
        if status is not None:
            query = query.where(ParseTask.status == status)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(Document.created_at.desc(), Document.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)).all()
        return Page[DocumentRead](items=[document_read(*row) for row in rows], page=page,
                                  page_size=page_size, total=total)

    def searchable_versions(self, document_ids: list[str] | None = None, *, index_name: str | None = None) -> dict[str, int]:
        query = select(Document.id, ParseTask.version).join(ParseTask, (
            (ParseTask.document_id == Document.id)
            & (ParseTask.version == Document.current_searchable_version)
        )).where(ParseTask.status == 'completed', ParseTask.retrieval_mode.in_(['bm25', 'hybrid']))
        if document_ids is not None:
            query = query.where(Document.id.in_(document_ids))
        if index_name is not None:
            query = query.where(ParseTask.index_name == index_name)
        return dict(self.session.execute(query).all())

    def has_processing_tasks(self) -> bool:
        return self.session.scalar(select(ParseTask.id).where(ParseTask.status == 'processing').limit(1)) is not None
