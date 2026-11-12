"""检索编排：三路召回（文本向量 / 图片向量 / BM25 词法）→ RRF → 重排序 → 分数门控。

RRF（Reciprocal Rank Fusion）只用名次不用原始分数，天然规避了
COSINE 相似度、CLIP 相似度、BM25 分三者不可比的问题；但它只保证「两路都靠前」，
并不保证「和问题真的相关」，所以后面还要用交叉编码器逐条重排序，
再用相对阈值把「沾边的噪声」挡掉——否则实测里 0.6 与 0.44 的结果会一起塞进上下文。

任何一层不可用都只降级、不阻断：没有 GPU → 退回 RRF 顺序；MySQL 读不到 →
只剩向量通道；模型没下载 → 只按 top_k 截断。
"""

from __future__ import annotations

import re
import time
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.tracing import span
from app.infra.db.engine import session_scope
from app.infra.db.models import Document
from app.infra.model import clip, embedding, reranker
from app.infra.vector import bm25_index
from app.infra.vector import milvus_client as mv

logger = get_logger("app.services.retrieval")

SOURCE_TEXT = "text"
SOURCE_IMAGE = "image"
SOURCE_BM25 = "bm25"

# 图片文件名由 MinerU 适配层生成：p003_02.png（第 3 页第 2 张）
IMAGE_PAGE_RE = re.compile(r"p(\d{3})_\d+")


def _fill_image_pages(hits: list[dict[str, Any]]) -> None:
    """图片集合没存页码，从文件名里还原，前端引用才能写「第几页的图」。"""
    for hit in hits:
        if hit.get("page_no"):
            continue
        match = IMAGE_PAGE_RE.search(str(hit.get("image_path") or ""))
        if match:
            hit["page_no"] = int(match.group(1))


def _fill_titles(hits: list[dict[str, Any]]) -> None:
    """给命中补上原始文件名，前端展示来源时不用再猜 8 位 doc_id。"""
    ids = {hit.get("doc_id") for hit in hits if hit.get("doc_id")}
    if not ids:
        return
    with session_scope() as session:
        rows = session.execute(select(Document.id, Document.filename).where(Document.id.in_(ids))).all()
    titles = {str(row[0]): str(row[1]) for row in rows}
    for hit in hits:
        hit["doc_title"] = titles.get(str(hit.get("doc_id") or ""), "")


def rrf_fuse(rankings: list[tuple[str, list[dict[str, Any]]]], k: int = 60) -> list[dict[str, Any]]:
    """score = Σ 1/(k + rank)。k 越大，头部优势越平缓。

    每条的 ``raw_scores`` 保留各通道自己的原始分（向量余弦 / BM25），
    只用于调试展示：三路分数不同量纲，不参与排序。
    """
    merged: dict[str, dict[str, Any]] = {}
    for source, ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            key = str(item.get("chunk_id"))
            entry = merged.get(key)
            if entry is None:
                entry = {
                    "chunk_id": key,
                    "doc_id": item.get("doc_id") or "",
                    "kind": item.get("kind") or ("image" if source == SOURCE_IMAGE else "text"),
                    "page_no": int(item.get("page_no") or 0),
                    "text": item.get("text") or "",
                    "image_path": item.get("image_path"),
                    "caption": item.get("caption") or "",
                    "raw_scores": {},
                    "score": 0.0,
                    "sources": [],
                }
                merged[key] = entry
            entry["score"] += 1.0 / (k + rank)
            raw = float(item.get("score") or 0.0)
            if raw >= entry["raw_scores"].get(source, float("-inf")):
                entry["raw_scores"][source] = raw
            if source not in entry["sources"]:
                entry["sources"].append(source)
            # 同一 chunk 被多路命中时，补齐别的通道才有的字段（正文 / 图片 / 页码 / 说明）
            for field in ("text", "image_path", "kind", "page_no", "caption"):
                if not entry.get(field) and item.get(field):
                    entry[field] = item[field]

    fused = sorted(merged.values(), key=lambda row: row["score"], reverse=True)
    for row in fused:
        row["rrf_score"] = row["score"]
        row["rerank_score"] = None
    return fused


def _is_image(hit: dict[str, Any]) -> bool:
    return bool(hit.get("image_path")) and not str(hit.get("text") or "").strip()


def order_by_rerank(
    items: list[dict[str, Any]],
    scores: list[float] | None,
    *,
    ratio: float,
    abs_min: float,
) -> list[dict[str, Any]]:
    """按重排序分数排序并做门控；``scores=None`` 表示重排序不可用，退回 RRF 顺序。

    门控阈值同时受两个约束：不低于最高分的 ``ratio``，也不低于绝对下限 ``abs_min``。
    只用一个相对阈值的话，「全部都是噪声」时最高分本身很低，仍会全保留。
    """
    if scores is None or len(scores) != len(items):
        return sorted(items, key=lambda item: float(item.get("score") or 0.0), reverse=True)

    scored: list[dict[str, Any]] = []
    for item, value in zip(items, scores):
        row = dict(item)
        row["rerank_score"] = float(value)
        row["score"] = float(value)
        scored.append(row)
    scored.sort(key=lambda row: row["score"], reverse=True)
    if not scored:
        return []
    floor = max(abs_min, scored[0]["score"] * ratio)
    return [row for row in scored if row["score"] >= floor]


def attach_images_by_page(
    text_hits: list[dict[str, Any]],
    image_hits: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """图片只在「同一页已有文本命中」时才保留，且每页最多留一张。

    图片通道是按整张图算相似度，天然更容易「沾边」；没有说明文字时它也无法
    证明自己回答了问题，所以用它给同页正文做插图，而不是让它单独进上下文。
    一页多图时只留排序最高的那张，否则同一页会占掉好几个最终名额。
    """
    page_scores: dict[tuple[str, int], float] = {}
    for hit in text_hits:
        key = (str(hit.get("doc_id") or ""), int(hit.get("page_no") or 0))
        page_scores[key] = max(page_scores.get(key, 0.0), float(hit.get("score") or 0.0))

    best_per_page: dict[tuple[str, int], dict[str, Any]] = {}
    for hit in image_hits:
        key = (str(hit.get("doc_id") or ""), int(hit.get("page_no") or 0))
        current = best_per_page.get(key)
        if current is None or float(hit.get("score") or 0.0) > float(current.get("score") or 0.0):
            best_per_page[key] = hit

    kept: list[dict[str, Any]] = []
    for key, hit in best_per_page.items():
        best = page_scores.get(key)
        if best is None:
            continue
        row = dict(hit)
        row["score"] = best  # 与所属页同分，折叠时才会紧跟在那一页后面
        kept.append(row)
    return kept


def search_with_stages(
    query: str,
    top_k: int | None = None,
    *,
    with_images: bool = True,
    doc_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, dict[str, int]]:
    """返回（命中列表, 总耗时毫秒, 各阶段耗时）。

    ``top_k`` 是最终条数上限，不传则用配置里的 ``final_top_k``；
    任何单路召回失败都只少一路结果，不影响其余链路。
    """
    settings = get_settings().retrieval
    stages: dict[str, int] = {}
    started = time.perf_counter()

    mark = time.perf_counter()
    with span("search.embed", query_len=len(query)):
        query_vector = embedding.embed_query(query)
    stages["embed_ms"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    with span("search.knn"):
        rankings: list[tuple[str, list[dict[str, Any]]]] = [
            (SOURCE_TEXT, mv.search_text(query_vector, settings.text_top_k, doc_id))
        ]
        if with_images:
            try:
                image_vector = clip.embed_texts([query])[0]
                rankings.append((SOURCE_IMAGE, mv.search_image(image_vector, settings.image_top_k, doc_id)))
            except Exception as exc:  # 图片通道不可用不该拖垮文本检索
                log_event(logger, "search_image_disabled", level=30, error=str(exc))
    stages["knn_ms"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    with span("search.bm25"):
        try:
            rankings.append((SOURCE_BM25, bm25_index.search_bm25(query, settings.bm25_top_k, doc_id)))
        except Exception as exc:  # 词法索引依赖 MySQL，读不到就只走向量
            log_event(logger, "search_bm25_disabled", level=30, error=str(exc))
    stages["bm25_ms"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    merged = rrf_fuse(rankings, settings.rrf_k)
    _fill_image_pages(merged)
    stages["fusion_ms"] = int((time.perf_counter() - mark) * 1000)

    mark = time.perf_counter()
    candidates = merged[: settings.rerank_top_n] if settings.rerank_top_n > 0 else merged
    text_pool = [hit for hit in candidates if not _is_image(hit)]
    image_pool = [hit for hit in merged if _is_image(hit)]
    scores = None
    if settings.rerank_enabled and text_pool:
        with span("search.rerank", candidates=len(text_pool)):
            scores = reranker.score(query, [str(hit.get("text") or "") for hit in text_pool])
    ordered = order_by_rerank(
        text_pool, scores, ratio=settings.gate_ratio, abs_min=settings.gate_abs_min
    )
    hits = ordered + attach_images_by_page(ordered, image_pool)
    hits.sort(key=lambda row: float(row.get("score") or 0.0), reverse=True)
    stages["rerank_ms"] = int((time.perf_counter() - mark) * 1000)

    limit = top_k if top_k and top_k > 0 else settings.final_top_k
    hits = hits[:limit]
    for hit in hits:
        hit["text"] = str(hit.get("text") or "")[: settings.max_snippet_chars]
    _fill_titles(hits)

    stages["total_ms"] = int((time.perf_counter() - started) * 1000)
    log_event(
        logger,
        "search_done",
        hits=len(hits),
        candidates=len(merged),
        reranked=scores is not None,
        took_ms=stages["total_ms"],
        with_images=with_images,
    )
    return hits, stages["total_ms"], stages


def search(
    query: str,
    top_k: int | None = None,
    *,
    with_images: bool = True,
    doc_id: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    """兼容旧调用点：只要（命中, 耗时）。"""
    hits, took_ms, _ = search_with_stages(query, top_k, with_images=with_images, doc_id=doc_id)
    return hits, took_ms


def ensure_ready() -> None:
    """启动自检：集合存在且可检索，否则抛 MODEL_UNAVAILABLE 触发 503。"""
    try:
        mv.ensure_collections()
    except Exception as exc:
        raise RuntimeError(f"向量库不可用: {exc}") from exc


__all__ = [
    "rrf_fuse",
    "order_by_rerank",
    "attach_images_by_page",
    "search",
    "search_with_stages",
    "ensure_ready",
]
