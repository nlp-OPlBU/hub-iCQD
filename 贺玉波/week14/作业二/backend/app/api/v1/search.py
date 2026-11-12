"""检索接口：文本 / 图片 / BM25 三路召回 → RRF → 重排序 → 分数门控。

同步函数交给 FastAPI 线程池执行，避免写入阻塞事件循环
（模型推理与 Milvus Lite 都是同步阻塞调用）。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.core.exceptions import AppError, ErrorCode
from app.services import retrieval_service

router = APIRouter(prefix="/search", tags=["search"])


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    top_k: int = Field(default=10, ge=1, le=50)
    with_images: bool = True
    doc_id: str | None = None


class SearchResponse(BaseModel):
    query: str
    took_ms: int
    count: int
    stages: dict[str, int] = Field(
        default_factory=dict, description="embed/knn/bm25/fusion/rerank/total 各阶段耗时"
    )
    hits: list[dict[str, Any]]


def _run(query: str, top_k: int, with_images: bool, doc_id: str | None) -> SearchResponse:
    try:
        hits, took_ms, stages = retrieval_service.search_with_stages(
            query, top_k, with_images=with_images, doc_id=doc_id
        )
    except Exception as exc:
        raise AppError(ErrorCode.MODEL_UNAVAILABLE, f"检索失败: {exc}") from exc
    return SearchResponse(query=query, took_ms=took_ms, count=len(hits), stages=stages, hits=hits)


@router.post("", response_model=SearchResponse)
def search(req: SearchRequest) -> SearchResponse:
    return _run(req.query, req.top_k, req.with_images, req.doc_id)


@router.get("", response_model=SearchResponse)
def search_get(
    q: str = Query(min_length=1, max_length=500, description="查询词"),
    top_k: int = Query(default=10, ge=1, le=50),
    with_images: bool = True,
    doc_id: str | None = None,
) -> SearchResponse:
    return _run(q, top_k, with_images, doc_id)
