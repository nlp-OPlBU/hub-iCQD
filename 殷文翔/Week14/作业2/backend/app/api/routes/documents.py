"""PDF upload and persistent document/task queries."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Path as PathParam, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.db.session import get_session
from app.repositories.document import DocumentRepository
from app.repositories.images import ImageRepository
from app.schemas.common import ErrorDetail, ErrorResponse, Page
from app.schemas.document import (BatchUploadResponse, DocumentPageRead, DocumentRead, EvidenceBlockRead, RetryRequest, TaskRead,
                                  TaskStatus, UploadAccepted, UploadRejected)
from app.services.evidence import read_evidence
from app.services.images import image_read, resource_path
from app.services.mineru_artifacts import evidence_type
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


@router.get('/documents/{document_id}/content')
def document_content(document_id: UUID, request: Request, session: DBSession,
                     version: Annotated[int | None, Query(ge=1)] = None):
    try:
        with session.begin():
            document, _ = DocumentRepository(session).source_record(str(document_id), version)
            try:
                path = resource_path(request.app.state.settings, document.storage_key)
            except ApiError:
                raise ApiError(404, 'DOCUMENT_FILE_UNAVAILABLE', '原始 PDF 不可用') from None
            filename = document.original_filename
    except SQLAlchemyError:
        raise ApiError(503, 'DATABASE_NOT_READY', '文档查询暂不可用', True) from None
    return FileResponse(path, media_type='application/pdf', filename=filename,
                        content_disposition_type='inline', headers={
                            'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600'})


@router.get('/documents/{document_id}/pages/{page}', response_model=DocumentPageRead)
def document_page(document_id: UUID, page: Annotated[int, PathParam(ge=1)],
                  request: Request, session: DBSession,
                  version: Annotated[int | None, Query(ge=1)] = None):
    settings = request.app.state.settings
    try:
        with session.begin():
            document, task = DocumentRepository(session).source_record(str(document_id), version)
            artifact = read_evidence(settings, task)
            selected = next((item for item in artifact['pages'] if item['page'] == page), None)
            if selected is None:
                raise ApiError(404, 'DOCUMENT_PAGE_NOT_FOUND', '文档页码不存在')
            blocks = []
            for block in selected['blocks']:
                kind = block.get('evidence_type', evidence_type(block['type']))
                images = ImageRepository(session).document_images(settings, {
                    'document_id': document.id, 'version': task.version, 'page': page,
                    'evidence_type': kind, 'image_keys': block['image_keys']}, context_limit=False)
                blocks.append(EvidenceBlockRead(block_index=block['block_index'], evidence_type=kind,
                    text=block['text'], text_origin=block.get('text_origin', 'content'),
                    bbox=block.get('bbox'), images=[image_read(row) for row in images]))
            return DocumentPageRead(document_id=document.id, version=task.version,
                filename=document.original_filename, page=page, page_count=len(artifact['pages']), blocks=blocks)
    except SQLAlchemyError:
        raise ApiError(503, 'DATABASE_NOT_READY', '文档查询暂不可用', True) from None
