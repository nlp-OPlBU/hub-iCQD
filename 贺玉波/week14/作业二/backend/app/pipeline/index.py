"""向量索引：切块写入 doc_text（1024 维），图片写入 doc_image（512 维）。

幂等：进入时先按 doc_id 删除旧向量，消息重投不会产生重复。
图片先从解析目录复制到静态目录，前端可直接通过 /static/images 访问。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.infra.model import clip, embedding
from app.infra.vector import milvus_client as mv

logger = get_logger("app.pipeline.index")

TEXT_BATCH_SIZE = 32
# Milvus 的 VARCHAR max_length 按 UTF-8 字节计（schema 里 text 是 8192 字节），
# 中文 3 字节/字，所以必须按字节截断，不能按字符数截。
MAX_TEXT_BYTES = 8000
STATIC_IMAGE_PREFIX = "/static/images"


def _truncate_bytes(text: str, limit: int = MAX_TEXT_BYTES) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[:limit].decode("utf-8", errors="ignore")


def _copy_images(doc_id: str, images: list[Path]) -> dict[str, str]:
    """复制图片到 data/images/<doc_id>/，返回 {源路径: 静态访问路径}。"""
    if not images:
        return {}
    target_dir = get_settings().storage.images / doc_id
    target_dir.mkdir(parents=True, exist_ok=True)
    mapping: dict[str, str] = {}
    for source in images:
        target = target_dir / source.name
        try:
            shutil.copy2(source, target)
        except OSError as exc:
            log_event(logger, "index_image_copy_failed", level=30, image=str(source), error=str(exc))
            continue
        mapping[str(source)] = f"{STATIC_IMAGE_PREFIX}/{doc_id}/{source.name}"
    return mapping


def _text_rows(doc_id: str, chunks: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for start in range(0, len(chunks), TEXT_BATCH_SIZE):
        batch = chunks[start : start + TEXT_BATCH_SIZE]
        vectors = embedding.embed_texts([item["text"] for item in batch])
        for chunk, vector in zip(batch, vectors):
            rows.append(
                {
                    "chunk_id": chunk["id"],
                    "doc_id": doc_id,
                    "kind": chunk["kind"],
                    "page_no": int(chunk["page_no"] or 0),
                    "text": _truncate_bytes(chunk["text"]),
                    "embedding": vector,
                }
            )
    return rows


def _image_rows(doc_id: str, images: list[Path]) -> list[dict]:
    mapping = _copy_images(doc_id, images)
    if not mapping:
        return []
    sources = [Path(item) for item in mapping]
    vectors = clip.embed_images(sources)
    rows: list[dict] = []
    for index, (source, vector) in enumerate(zip(sources, vectors)):
        if vector is None:
            continue
        rows.append(
            {
                "chunk_id": f"{doc_id}-img-{index:03d}",
                "doc_id": doc_id,
                "image_path": mapping[str(source)][:500],
                "caption": "",
                "embedding": vector,
            }
        )
    return rows


def index_document(doc_id: str, chunks: list[dict], images: list[Path]) -> tuple[int, int]:
    """写入向量并返回（文本块数, 图片数）。异常向上抛，由 worker 决定重试。"""
    mv.delete_doc_vectors(doc_id)
    text_rows = _text_rows(doc_id, chunks)
    mv.upsert_text_vectors(text_rows)
    image_rows = _image_rows(doc_id, images)
    mv.upsert_image_vectors(image_rows)
    log_event(logger, "index_done", ref_id=doc_id, text_rows=len(text_rows), image_rows=len(image_rows))
    return len(text_rows), len(image_rows)


def drop_document(doc_id: str) -> None:
    """删除文档时同步清空向量。"""
    mv.delete_doc_vectors(doc_id)
