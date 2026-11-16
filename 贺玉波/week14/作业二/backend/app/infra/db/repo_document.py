"""document / chunk 表读写。

事务边界由调用方（service / pipeline）决定：这里只 flush，不 commit。
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.infra.db.models import Chunk, Document


def create_document(
    session: Session,
    *,
    doc_id: str,
    filename: str,
    sha256: str,
    size_bytes: int,
) -> Document:
    doc = Document(id=doc_id, filename=filename, sha256=sha256, size_bytes=size_bytes)
    session.add(doc)
    session.flush()
    return doc


def get_document(session: Session, doc_id: str) -> Document | None:
    return session.get(Document, doc_id)


def get_document_by_sha(session: Session, sha256: str) -> Document | None:
    return session.scalar(select(Document).where(Document.sha256 == sha256))


def list_documents(
    session: Session,
    *,
    limit: int = 20,
    offset: int = 0,
    status: str | None = None,
) -> tuple[list[Document], int]:
    stmt = select(Document)
    if status:
        stmt = stmt.where(Document.status == status)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(stmt.order_by(Document.created_at.desc()).limit(limit).offset(offset)).all()
    return list(rows), int(total)


def update_document(session: Session, doc_id: str, **fields: Any) -> Document | None:
    doc = session.get(Document, doc_id)
    if doc is None:
        return None
    for key, value in fields.items():
        if not hasattr(doc, key):
            raise AttributeError(f"document 无字段 {key}")
        setattr(doc, key, value)
    session.flush()
    return doc


def bump_attempts(session: Session, doc_id: str) -> int:
    doc = session.get(Document, doc_id)
    if doc is None:
        return 0
    doc.attempts += 1
    session.flush()
    return doc.attempts


def replace_chunks(session: Session, doc_id: str, chunks: Iterable[dict[str, Any]]) -> int:
    """整篇替换：先删旧块再批量插入，保证重跑幂等。"""
    session.execute(delete(Chunk).where(Chunk.doc_id == doc_id))
    rows = [Chunk(**item) for item in chunks]
    session.add_all(rows)
    session.flush()
    return len(rows)


def list_chunks(session: Session, doc_id: str, *, limit: int = 200, offset: int = 0) -> Sequence[Chunk]:
    stmt = select(Chunk).where(Chunk.doc_id == doc_id).order_by(Chunk.idx).limit(limit).offset(offset)
    return session.scalars(stmt).all()


def count_chunks(session: Session, doc_id: str) -> int:
    return int(session.scalar(select(func.count()).select_from(Chunk).where(Chunk.doc_id == doc_id)) or 0)
