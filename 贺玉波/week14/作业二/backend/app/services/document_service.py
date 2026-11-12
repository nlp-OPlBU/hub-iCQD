"""文档业务编排：上传去重、入库、投递、查询、失败重投。

API 层只做参数适配，所有跨组件动作都在这里完成，便于测试与复用。
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from fastapi import UploadFile

from app.core.config import get_settings
from app.core.exceptions import AppError, ConflictStateError, ErrorCode, NotFoundError
from app.core.logging import get_logger, get_trace_id, log_event
from app.domain.enums import DocStatus
from app.domain.state_machine import ensure_transition
from app.infra.db import repo_document as repo
from app.infra.db.engine import session_scope
from app.infra.db.models import Document
from app.infra.mq.kafka_client import publish

logger = get_logger("app.services.document")

# 当前阶段只接 PDF；图片单文件上传与图文混排在 P4 一起做
ALLOWED_SUFFIXES = {".pdf"}
READ_BLOCK = 1024 * 1024


def snapshot(doc: Document) -> dict[str, Any]:
    return {column.name: getattr(doc, column.name) for column in Document.__table__.columns}


async def create_from_upload(upload: UploadFile) -> tuple[dict[str, Any], bool]:
    """保存上传文件并投递解析任务。

    返回 ``(文档快照, 是否新建)``：同一 sha256 重复上传时返回已存在的文档且不重复入队。
    """
    settings = get_settings()
    filename = Path(upload.filename or "upload.pdf").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise AppError(ErrorCode.VALIDATION_ERROR, f"不支持的文件类型 {suffix or '(无扩展名)'}，当前仅支持 PDF")

    limit_bytes = settings.storage.max_upload_mb * 1024 * 1024
    tmp_path = settings.storage.raw / f".upload-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0

    try:
        with tmp_path.open("wb") as fp:
            while block := await upload.read(READ_BLOCK):
                size += len(block)
                if size > limit_bytes:
                    raise AppError(
                        ErrorCode.FILE_TOO_LARGE,
                        f"文件超过 {settings.storage.max_upload_mb}MB 限制",
                        details={"limit_mb": settings.storage.max_upload_mb},
                    )
                digest.update(block)
                fp.write(block)
        if size == 0:
            raise AppError(ErrorCode.NO_CONTENT, "文件内容为空")

        sha256 = digest.hexdigest()
        with session_scope() as session:
            existing = repo.get_document_by_sha(session, sha256)
            if existing is not None:
                tmp_path.unlink(missing_ok=True)
                log_event(logger, "upload_deduped", ref_id=existing.id, sha256=sha256, size=size)
                return snapshot(existing), False

            doc_id = uuid.uuid4().hex
            final_path = settings.storage.raw / f"{doc_id}{suffix}"
            tmp_path.replace(final_path)

            doc = repo.create_document(
                session, doc_id=doc_id, filename=filename, sha256=sha256, size_bytes=size
            )
            data = snapshot(doc)

        payload = {
            "v": 1,
            "doc_id": doc_id,
            "path": str(final_path),
            "filename": filename,
            "sha256": sha256,
            "trace_id": get_trace_id(),
        }
        try:
            publish(settings.kafka.topic, doc_id, payload)
        except Exception as exc:
            final_path.unlink(missing_ok=True)
            with session_scope() as session:
                stale = repo.get_document(session, doc_id)
                if stale is not None:
                    session.delete(stale)
            log_event(logger, "upload_enqueue_failed", level=40, ref_id=doc_id, error=str(exc))
            raise AppError(ErrorCode.MQ_UNAVAILABLE, "任务队列不可用，请稍后重试") from exc

        log_event(logger, "upload_accepted", ref_id=doc_id, filename=filename, size=size)
        return data, True
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def list_documents(*, limit: int = 20, offset: int = 0, status: str | None = None) -> tuple[list[dict], int]:
    with session_scope() as session:
        rows, total = repo.list_documents(session, limit=limit, offset=offset, status=status)
        return [snapshot(row) for row in rows], total


def get_document(doc_id: str) -> dict[str, Any]:
    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        return snapshot(doc)


def list_chunks(doc_id: str, *, limit: int = 200, offset: int = 0) -> list[dict[str, Any]]:
    with session_scope() as session:
        if repo.get_document(session, doc_id) is None:
            raise NotFoundError("文档不存在")
        rows = repo.list_chunks(session, doc_id, limit=limit, offset=offset)
        return [
            {
                "id": row.id,
                "idx": row.idx,
                "kind": row.kind,
                "page_no": row.page_no,
                "text": row.text,
                "image_path": row.image_path,
            }
            for row in rows
        ]


def retry_document(doc_id: str) -> dict[str, Any]:
    """failed → pending 并重新投递；attempts 归零，给人工重试一次完整预算。"""
    settings = get_settings()
    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        if doc is None:
            raise NotFoundError("文档不存在")
        if doc.status != DocStatus.FAILED.value:
            raise ConflictStateError(
                f"只有 failed 状态可重试，当前为 {doc.status}",
                details={"status": doc.status},
            )
        ensure_transition(doc.status, DocStatus.PENDING)
        repo.update_document(
            session,
            doc_id,
            status=DocStatus.PENDING.value,
            stage=None,
            progress=0,
            attempts=0,
            error_code=None,
            error_message=None,
        )
        data = snapshot(repo.get_document(session, doc_id))

    payload = {
        "v": 1,
        "doc_id": doc_id,
        "path": str(settings.storage.raw / f"{doc_id}.pdf"),
        "filename": data["filename"],
        "sha256": data["sha256"],
        "trace_id": get_trace_id(),
        "retry": True,
    }
    try:
        publish(settings.kafka.topic, doc_id, payload)
    except Exception as exc:
        with session_scope() as session:
            repo.update_document(
                session,
                doc_id,
                status=DocStatus.FAILED.value,
                error_code=ErrorCode.MQ_UNAVAILABLE.value,
                error_message=str(exc),
            )
        raise AppError(ErrorCode.MQ_UNAVAILABLE, "任务队列不可用，请稍后重试") from exc

    log_event(logger, "document_retried", ref_id=doc_id)
    return data
