"""离线重建向量索引。

用途：换 embedding 模型、集合被清空、调整切分参数后，不必重新跑 MinerU（解析最慢），
直接从 MySQL 里的分块与磁盘上的图片重建向量。

用法：``python -m app.worker.reindex [--doc-id xxx]``
"""

from __future__ import annotations

import argparse

from app.core.config import get_settings
from app.core.logging import get_logger, log_event, setup_logging
from app.domain.enums import DocStatus
from app.infra.db import repo_document as repo
from app.infra.db.engine import session_scope
from app.infra.db.models import Document
from app.infra.vector.milvus_client import ensure_collections
from app.pipeline import index

logger = get_logger("app.worker.reindex")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}


def _targets(doc_id: str | None) -> list[str]:
    with session_scope() as session:
        if doc_id:
            return [doc_id] if repo.get_document(session, doc_id) else []
        rows = session.query(Document.id).filter(Document.status == DocStatus.DONE.value).all()
        return [row[0] for row in rows]


def _chunks(doc_id: str) -> list[dict]:
    with session_scope() as session:
        return [
            {
                "id": row.id,
                "doc_id": row.doc_id,
                "idx": row.idx,
                "kind": row.kind,
                "page_no": row.page_no,
                "text": row.text,
            }
            for row in repo.list_chunks(session, doc_id, limit=10000)
        ]


def rebuild(doc_id: str) -> tuple[int, int]:
    images = sorted(
        item
        for item in (get_settings().storage.parsed / doc_id).rglob("*")
        if item.is_file() and item.suffix.lower() in IMAGE_SUFFIXES
    )
    chunks = _chunks(doc_id)
    if not chunks:
        log_event(logger, "reindex_skip_empty", ref_id=doc_id, level=30)
        return 0, 0
    return index.index_document(doc_id, chunks, images)


def main() -> None:
    parser = argparse.ArgumentParser(description="重建向量索引")
    parser.add_argument("--doc-id", default=None, help="只重建指定文档")
    args = parser.parse_args()

    setup_logging()
    ensure_collections()
    targets = _targets(args.doc_id)
    log_event(logger, "reindex_start", docs=len(targets))
    total_text = total_image = 0
    for doc_id in targets:
        text_rows, image_rows = rebuild(doc_id)
        total_text += text_rows
        total_image += image_rows
    log_event(logger, "reindex_done", docs=len(targets), text_rows=total_text, image_rows=total_image)


if __name__ == "__main__":
    main()
