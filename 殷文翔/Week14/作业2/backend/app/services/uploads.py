"""Upload coordination; no Kafka send, MinerU process, or PDF parsing here."""
import logging

from sqlalchemy.exc import SQLAlchemyError
from starlette.datastructures import UploadFile

from app.api.errors import ApiError
from app.core.config import Settings
from app.db.base import new_id
from app.repositories.document import DocumentRepository, document_read
from app.repositories.outbox import add_parse_requested
from app.schemas.document import DocumentRead, ParseEvent
from app.services.storage import FileStorage

logger = logging.getLogger(__name__)


def check_database(factory) -> None:
    try:
        with factory() as session, session.begin():
            DocumentRepository(session).check_ready()
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_NOT_READY", "MySQL 不可用或尚未迁移", True) from None


def accept_upload(upload: UploadFile, settings: Settings, factory, storage: FileStorage) -> DocumentRead:
    document_id, task_id = new_id(), new_id()
    try:
        saved = storage.save_pdf(upload.file, upload.filename or "", upload.content_type, document_id, task_id)
    except (OSError, ApiError) as exc:
        storage.discard_uncommitted(document_id)
        if isinstance(exc, ApiError):
            raise
        raise ApiError(503, "STORAGE_UNAVAILABLE", "文件存储暂不可用", True) from None

    commit_started = False
    try:
        with factory() as session, session.begin():
            document, task = DocumentRepository(session).add_upload(
                document_id=document_id, task_id=task_id, filename=saved.filename,
                storage_key=saved.storage_key, size_bytes=saved.size_bytes, sha256=saved.sha256,
                settings=settings,
            )
            add_parse_requested(session, settings, ParseEvent(
                event_id=new_id(), document_id=document_id, task_id=task_id,
                version=1, storage_key=saved.storage_key,
            ))
            session.flush()
            result = document_read(document, task)  # Validate before COMMIT.
            commit_started = True
    except SQLAlchemyError:
        if not commit_started:
            storage.discard_uncommitted(document_id)
            raise ApiError(503, "DATABASE_NOT_READY", "数据库写入失败", True) from None
        # COMMIT can have succeeded even when its acknowledgement is lost. A new
        # primary DB session may confirm success; absence is NOT proof of rollback.
        confirmed = None
        try:
            with factory() as session, session.begin():
                confirmed = DocumentRepository(session).get(document_id)
        except SQLAlchemyError:
            confirmed = None
        if confirmed is not None and str(confirmed.latest_task.id) == task_id:
            storage.forget_receipt(document_id)
            return confirmed
        logger.error("Upload commit unresolved document_id=%s task_id=%s", document_id, task_id)
        raise ApiError(503, "UPLOAD_COMMIT_UNCERTAIN", "提交结果待核对，请先查询文档列表，勿自动重传",
                       details={"compensation_required": True}) from None
    except BaseException:
        if not commit_started:
            storage.discard_uncommitted(document_id)
        raise
    storage.forget_receipt(document_id)  # Cleanup failure cannot undo a successful commit.
    return result
