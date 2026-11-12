"""多模态问答接口：文本提问 + 可选图片一起送多模态大模型。"""

import base64
import io
import logging
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..config import get_settings
from ..database import get_db
from ..models import Document, DocumentStatus
from ..schemas import ChatResponse
from ..services.llm import LLMNotConfigured
from ..services.media import UserImage
from ..services.rag import RAGService

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()
rag_service = RAGService(settings)


@router.post("/chat", response_model=ChatResponse)
async def chat(
    question: str = Form(default=""),
    top_k: int = Form(default=settings.chat_top_k),
    doc_ids: str | None = Form(default=None),
    images: list[UploadFile] | None = File(default=None),
    db: Session = Depends(get_db),
):
    question = (question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    top_k = max(1, min(int(top_k), 20))
    images = images or []

    # 只在“解析完成”的文档里检索，避免命中解析失败的半成品数据
    completed_ids = set(
        db.scalars(
            select(Document.id).where(Document.status == DocumentStatus.COMPLETED)
        ).all()
    )
    if not completed_ids:
        raise HTTPException(
            status_code=400,
            detail="还没有解析完成的文档，请先上传 PDF 并等待解析完成",
        )

    requested_ids = _parse_doc_ids(doc_ids)
    allowed_ids = completed_ids & set(requested_ids) if requested_ids else completed_ids
    if not allowed_ids:
        raise HTTPException(
            status_code=400, detail="指定的文档不存在或尚未解析完成"
        )

    if len(images) > settings.max_user_images:
        raise HTTPException(
            status_code=400,
            detail=f"最多同时上传 {settings.max_user_images} 张图片",
        )

    user_images: list[UserImage] = []
    for upload in images:
        user_images.append(await _read_user_image(upload))

    try:
        result: ChatResponse = await run_in_threadpool(
            rag_service.answer,
            question,
            user_images,
            top_k,
            sorted(allowed_ids),
        )
    except LLMNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("问答失败")
        raise HTTPException(status_code=502, detail=f"问答失败：{exc}") from exc

    return result


async def _read_user_image(upload: UploadFile) -> UserImage:
    data = await upload.read()
    filename = upload.filename or "image"

    if not data:
        raise HTTPException(status_code=400, detail=f"图片为空：{filename}")
    limit = settings.max_image_bytes_mb * 1024 * 1024
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"图片超过 {settings.max_image_bytes_mb} MB：{filename}",
        )

    try:
        with Image.open(io.BytesIO(data)) as probe:
            probe.load()
            image_format = (probe.format or "PNG").upper()
            pil_image = probe.convert("RGB")
    except Exception as exc:
        raise HTTPException(
            status_code=400, detail=f"无法读取图片：{filename}"
        ) from exc

    mime = Image.MIME.get(image_format, "image/png")
    data_url = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return UserImage(data_url=data_url, pil=pil_image, filename=filename)


def _parse_doc_ids(raw: str | None) -> list[int]:
    if not raw:
        return []
    parts = re.split(r"[,\s;]+", raw.strip().strip("[]"))
    return [int(part) for part in parts if part.isdigit()]
