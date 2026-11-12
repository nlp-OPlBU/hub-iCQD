"""Document persistence and snapshot reads. Transaction boundaries belong to callers."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Document, OutboxEvent, ParseTask
from app.schemas.common import ErrorDetail, Page
from app.schemas.document import DocumentRead, TaskRead


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

    def list(self, page: int, page_size: int, status: str | None) -> Page[DocumentRead]:
        query = self.latest_query()
        if status is not None:
            query = query.where(ParseTask.status == status)
        total = self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = self.session.execute(query.order_by(Document.created_at.desc(), Document.id.desc())
                                    .offset((page - 1) * page_size).limit(page_size)).all()
        return Page[DocumentRead](items=[document_read(*row) for row in rows], page=page,
                                  page_size=page_size, total=total)
