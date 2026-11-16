"""文档接口：上传、列表、详情、分块、失败重投。

只做 HTTP 适配（参数校验、状态码），业务在 ``app.services.document_service``。
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, File, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict

from app.services import document_service as service

router = APIRouter(prefix="/documents", tags=["documents"])


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    size_bytes: int
    page_count: int | None = None
    status: str
    stage: str | None = None
    progress: int
    attempts: int
    chunk_count: int
    image_count: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    total: int
    items: list[DocumentOut]


class UploadResult(BaseModel):
    created: bool
    document: DocumentOut


class ChunkOut(BaseModel):
    id: str
    idx: int
    kind: str
    page_no: int | None = None
    text: str
    image_path: str | None = None


@router.post(
    "",
    response_model=UploadResult,
    status_code=202,
    responses={200: {"model": UploadResult, "description": "文件已存在（sha256 相同），返回已有文档且不重复入队"}},
    summary="上传 PDF 并投递解析任务",
)
async def upload_document(
    response: Response,
    file: Annotated[UploadFile, File(description="待解析的 PDF 文件")],
) -> UploadResult:
    data, created = await service.create_from_upload(file)
    # 重复上传（sha256 已存在）返回 200，不重复入队
    response.status_code = 202 if created else 200
    return UploadResult(created=created, document=DocumentOut.model_validate(data))


@router.get("", response_model=DocumentList, summary="文档列表")
async def list_documents(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status: Annotated[str | None, Query(description="按状态过滤")] = None,
) -> DocumentList:
    items, total = await asyncio.to_thread(
        service.list_documents, limit=limit, offset=offset, status=status
    )
    return DocumentList(total=total, items=[DocumentOut.model_validate(item) for item in items])


@router.get("/{doc_id}", response_model=DocumentOut, summary="文档详情")
async def get_document(doc_id: str) -> DocumentOut:
    data = await asyncio.to_thread(service.get_document, doc_id)
    return DocumentOut.model_validate(data)


@router.get("/{doc_id}/chunks", response_model=list[ChunkOut], summary="文档分块")
async def get_chunks(
    doc_id: str,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChunkOut]:
    rows = await asyncio.to_thread(service.list_chunks, doc_id, limit=limit, offset=offset)
    return [ChunkOut.model_validate(row) for row in rows]


@router.post("/{doc_id}/retry", response_model=DocumentOut, status_code=202, summary="失败重投")
async def retry_document(doc_id: str) -> DocumentOut:
    data = await asyncio.to_thread(service.retry_document, doc_id)
    return DocumentOut.model_validate(data)
