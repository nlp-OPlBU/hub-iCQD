"""文档管理接口：上传 -> 投递 Kafka；查询状态；删除；重试。"""

import logging
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import Document, DocumentStatus
from ..schemas import DeleteResponse, DocumentOut, RetryResponse, UploadResponse
from ..services.kafka_producer import parse_task_producer
from ..services.vector_store import VectorStore

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
vector_store = VectorStore(settings)


@router.get("", response_model=list[DocumentOut])
def list_documents(
    status: DocumentStatus | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """前端轮询这个接口展示 待解析 / 解析中 / 解析完成 / 解析失败。"""
    stmt = select(Document).order_by(Document.created_at.desc())
    if status is not None:
        stmt = stmt.where(Document.status == status)
    return list(db.scalars(stmt))


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    return document


@router.post("/upload", response_model=UploadResponse)
def upload_documents(
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
):
    """一次可上传多个 PDF。

    每个文件：落盘 -> 写元信息(status=pending) -> 投递 Kafka。
    HTTP 请求不等待解析，解析由 worker 异步完成。
    """
    if not files:
        raise HTTPException(status_code=400, detail="请选择 PDF 文件")

    for upload in files:
        if Path(upload.filename or "").suffix.lower() != ".pdf":
            raise HTTPException(
                status_code=400, detail=f"只支持 PDF 文件：{upload.filename}"
            )

    documents: list[Document] = []
    kafka_error: str | None = None

    for upload in files:
        stored_name = f"{uuid4().hex}.pdf"
        stored_path = settings.upload_path / stored_name
        file_size = _save_upload(upload, stored_path)

        document = Document(
            original_name=upload.filename or stored_name,
            stored_name=stored_name,
            stored_path=str(stored_path),
            file_size=file_size,
            status=DocumentStatus.PENDING,
        )
        db.add(document)
        db.commit()
        db.refresh(document)

        if kafka_error is None:
            try:
                parse_task_producer.publish_parse_task(
                    document_id=document.id,
                    original_name=document.original_name,
                    stored_path=document.stored_path,
                )
            except Exception as exc:
                logger.exception("投递 Kafka 解析任务失败")
                kafka_error = f"提交 Kafka 解析任务失败：{exc}"

        if kafka_error is not None:
            document.status = DocumentStatus.FAILED
            document.error_message = kafka_error
            db.commit()
            db.refresh(document)

        documents.append(document)

    return UploadResponse(
        documents=[DocumentOut.model_validate(document) for document in documents]
    )


@router.post("/{document_id}/retry", response_model=RetryResponse)
def retry_document(document_id: int, db: Session = Depends(get_db)):
    """把失败的文档重新投递到 Kafka。"""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")
    if document.status == DocumentStatus.PARSING:
        raise HTTPException(status_code=409, detail="文档正在解析中，请稍后")

    document.status = DocumentStatus.PENDING
    document.error_message = None
    db.commit()
    db.refresh(document)

    try:
        parse_task_producer.publish_parse_task(
            document_id=document.id,
            original_name=document.original_name,
            stored_path=document.stored_path,
        )
        message = "重试任务已提交"
    except Exception as exc:
        logger.exception("重试投递 Kafka 失败")
        document.status = DocumentStatus.FAILED
        document.error_message = f"提交 Kafka 解析任务失败：{exc}"
        db.commit()
        db.refresh(document)
        message = "重试任务提交失败，请检查 Kafka"

    return RetryResponse(document=DocumentOut.model_validate(document), message=message)


@router.delete("/{document_id}", response_model=DeleteResponse)
def delete_document(document_id: int, db: Session = Depends(get_db)):
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=404, detail="文档不存在")

    # 向量、原始 PDF、MinerU 产物都尽量清理；任一步失败不阻塞删除记录
    try:
        vector_store.delete_by_doc(document_id)
    except Exception:
        logger.exception("删除 Milvus 向量失败: document_id=%s", document_id)

    _remove_file_safely(document.stored_path, settings.upload_path)
    if document.parsed_dir:
        _remove_dir_safely(document.parsed_dir, settings.processed_path)

    db.delete(document)
    db.commit()
    return DeleteResponse(message="删除成功")


# ---------------- 工具函数 ----------------
def _save_upload(upload: UploadFile, destination: Path) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    limit = settings.max_upload_size_mb * 1024 * 1024
    total = 0

    try:
        with destination.open("wb") as output:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise HTTPException(
                        status_code=413,
                        detail=f"文件超过 {settings.max_upload_size_mb} MB：{upload.filename}",
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise

    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"空文件：{upload.filename}")
    return total


def _remove_file_safely(path_str: str, root: Path) -> None:
    try:
        path = Path(path_str).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        logger.warning("拒绝删除越界路径: %s", path_str)
        return
    path.unlink(missing_ok=True)


def _remove_dir_safely(path_str: str, root: Path) -> None:
    try:
        path = Path(path_str).resolve()
        path.relative_to(root.resolve())
    except (ValueError, OSError):
        logger.warning("拒绝删除越界目录: %s", path_str)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
