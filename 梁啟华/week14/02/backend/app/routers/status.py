"""
状态查询路由 - 查询单个或批量任务状态
"""
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from ..db import get_db
from ..models import DocumentInfo, DocumentListResponse, DocumentStatus, StatusResponse

router = APIRouter(prefix="/api", tags=["状态查询"])


@router.get("/status/{task_id}", response_model=StatusResponse)
async def get_status(task_id: str):
    """查询单个任务状态"""
    db = get_db()
    doc = await db.get_document(task_id)
    if not doc:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return StatusResponse(
        task_id=task_id,
        status=DocumentStatus(doc['status']),
        error_message=doc.get('error_message'),
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    status: Optional[str] = Query(None, description="按状态筛选: pending/parsing/completed/error"),
):
    """获取文档列表，可按状态筛选"""
    db = get_db()
    status_enum = None
    if status:
        try:
            status_enum = DocumentStatus(status)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"无效状态值: {status}，可选: pending/parsing/completed/error"
            )

    docs = await db.list_documents(status_enum)
    documents = [
        DocumentInfo(
            task_id=d['task_id'],
            file_name=d['file_name'],
            file_path=d['file_path'],
            file_size=d['file_size'],
            status=DocumentStatus(d['status']),
            error_message=d.get('error_message'),
            created_at=d['created_at'],
            updated_at=d['updated_at'],
            parsed_text_preview=d.get('parsed_text_preview'),
        )
        for d in docs
    ]
    return DocumentListResponse(total=len(documents), documents=documents)


@router.get("/documents/stats")
async def get_doc_stats():
    """获取各状态文档数量统计"""
    db = get_db()
    docs = await db.list_documents()
    stats = {s.value: 0 for s in DocumentStatus}
    for d in docs:
        stats[d['status']] = stats.get(d['status'], 0) + 1
    stats['total'] = len(docs)
    return stats
