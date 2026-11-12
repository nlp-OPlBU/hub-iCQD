"""
文件上传路由 - 支持多文件上传，发送任务到 Kafka
"""
import uuid
from pathlib import Path
from typing import List

from fastapi import APIRouter, File, UploadFile, HTTPException
from loguru import logger

from ..config_loader import Config
from ..db import get_db
from ..models import UploadResponse
from ..kafka.producer import get_producer

router = APIRouter(prefix="/api/upload", tags=["文件上传"])


def _validate_file(file: UploadFile, cfg_upload: dict):
    allowed_exts = tuple(cfg_upload.get('allowed_extensions', ['.pdf']))
    ext = Path(file.filename).suffix.lower()
    if ext not in allowed_exts:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件类型: {ext}，仅支持 {allowed_exts}"
        )


@router.post("/pdfs", response_model=UploadResponse)
async def upload_pdfs(files: List[UploadFile] = File(...)):
    cfg = Config.get_section('upload')
    cfg_storage = Config.get_section('storage')

    max_count = cfg.get('max_file_count', 10)
    if len(files) > max_count:
        raise HTTPException(status_code=400, detail=f"单次最多上传 {max_count} 个文件")

    upload_dir = Path(cfg_storage['upload_dir'])
    upload_dir.mkdir(parents=True, exist_ok=True)

    db = get_db()
    producer = get_producer()
    task_ids = []

    for file in files:
        _validate_file(file, cfg)
        task_id = uuid.uuid4().hex[:16]
        file_ext = Path(file.filename).suffix.lower()
        save_path = upload_dir / f"{task_id}{file_ext}"

        content = await file.read()
        file_size = len(content)
        save_path.write_bytes(content)
        logger.info(f"文件已保存: {save_path} ({file_size} bytes)")

        await db.create_document(
            task_id=task_id,
            file_name=file.filename,
            file_path=str(save_path),
            file_size=file_size,
        )

        producer.send_upload_task(
            task_id=task_id,
            file_path=str(save_path),
            file_name=file.filename,
            file_size=file_size,
        )
        task_ids.append(task_id)

    return UploadResponse(
        success=True,
        task_ids=task_ids,
        message=f"成功接收 {len(task_ids)} 个文件，已提交解析队列",
    )
