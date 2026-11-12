"""PDF upload and persistent document/task queries."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.db.session import get_session
from app.repositories.document import DocumentRepository
from app.schemas.common import ErrorDetail, ErrorResponse, Page
from app.schemas.document import (BatchUploadResponse, DocumentRead, RetryRequest, TaskRead,
                                  TaskStatus, UploadAccepted, UploadRejected)
from app.services.storage import FileStorage
from app.services.upload_multipart import staged_files
from app.services.uploads import accept_upload, check_database

router = APIRouter(tags=["documents"], responses={503: {"model": ErrorResponse}})
DBSession = Annotated[Session, Depends(get_session)]


@router.post("/documents/batch", response_model=BatchUploadResponse,
             responses={400: {"model": ErrorResponse}, 413: {"model": ErrorResponse},
                        415: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
             openapi_extra={"requestBody": {"required": True, "content": {"multipart/form-data": {
                 "schema": {"type": "object", "required": ["files"], "properties": {
                     "files": {"type": "array", "items": {"type": "string", "format": "binary"}}}}}}}})
async def upload_batch(request: Request):
    results = []
    try:
        async with staged_files(request) as files:
            factory = request.app.state.session_factory
            await run_in_threadpool(check_database, factory)
            storage = FileStorage(request.app.state.settings)
            for index, file in enumerate(files):
                try:
                    document = await run_in_threadpool(accept_upload, file, request.app.state.settings,
                                                       factory, storage)
                    results.append(UploadAccepted(index=index, filename=file.filename or "", document=document))
                except ApiError as exc:
                    results.append(UploadRejected(index=index, filename=file.filename or "", status_code=exc.status,
                        error=ErrorDetail(code=exc.code, message=exc.message, retryable=exc.retryable,
                                          details=exc.details)))
    except OSError:
        raise ApiError(503, "STORAGE_UNAVAILABLE", "上传暂存空间不可用", True) from None
    accepted = sum(result.outcome == "accepted" for result in results)
    return BatchUploadResponse(request_id=request.state.request_id, accepted=accepted,
                               rejected=len(results) - accepted, results=results)


@router.get("/documents", response_model=Page[DocumentRead])
def list_documents(session: DBSession, page: Annotated[int, Query(ge=1)] = 1,
                   page_size: Annotated[int, Query(ge=1, le=100)] = 20, status: TaskStatus | None = None):
    try:
        with session.begin():
            return DocumentRepository(session).list(page, page_size, status)
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_NOT_READY", "文档查询暂不可用", True) from None


@router.get("/documents/{document_id}", response_model=DocumentRead,
            responses={404: {"model": ErrorResponse}})
def get_document(document_id: UUID, session: DBSession):
    try:
        with session.begin():
            result = DocumentRepository(session).get(str(document_id))
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_NOT_READY", "文档查询暂不可用", True) from None
    if result is None:
        raise ApiError(404, "DOCUMENT_NOT_FOUND", "文档不存在")
    return result


@router.get("/tasks/{task_id}", response_model=TaskRead, responses={404: {"model": ErrorResponse}})
def get_task(task_id: UUID, session: DBSession):
    try:
        with session.begin():
            result = DocumentRepository(session).get_task(str(task_id))
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_NOT_READY", "任务查询暂不可用", True) from None
    if result is None:
        raise ApiError(404, "TASK_NOT_FOUND", "任务不存在")
    return result


@router.post("/documents/{document_id}/retry", response_model=TaskRead,
             status_code=status.HTTP_202_ACCEPTED,
             responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}})
def retry_document(document_id: UUID, payload: RetryRequest, request: Request,
                   idempotency_key: Annotated[UUID, Header(alias="Idempotency-Key")],
                   session: DBSession):
    try:
        with session.begin():
            return DocumentRepository(session).retry(
                str(document_id), payload.expected_latest_version, str(idempotency_key),
                request.app.state.settings)
    except SQLAlchemyError:
        raise ApiError(503, "DATABASE_NOT_READY", "数据库暂不可用", True) from None
