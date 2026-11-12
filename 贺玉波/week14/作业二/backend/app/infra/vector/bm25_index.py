"""BM25 稀疏召回：补向量召回的短板。

实测向量通道对「专有名词、缩写、编号、表格里的字段名」不敏感，
这些问题用词法检索反而更准；两路一起进 RRF，比单纯加大 top_k 有效。

实现刻意不引入新依赖：分词用正则（中文按单字 + 双字 bigram，英文按词），
倒排表常驻内存，用 MySQL ``chunk`` 表的 (行数, 最新 created_at) 判断是否需要重建。
"""

from __future__ import annotations

import math
import re
import threading
from typing import Any

from sqlalchemy import func, select

from app.core.logging import get_logger, log_event
from app.infra.db.engine import session_scope
from app.infra.db.models import Chunk

logger = get_logger("app.infra.bm25")

CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
ASCII_WORD = re.compile(r"[a-z0-9]+")

K1 = 1.5
B = 0.75


def tokenize(text: str) -> list[str]:
    """中文单字 + 双字 bigram，英文/数字按词；不做停用词表，靠 idf 自然降权。"""
    lowered = (text or "").lower()
    tokens = ASCII_WORD.findall(lowered)
    for run in CJK_RUN.findall(lowered):
        tokens.extend(run)
        tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


class BM25Index:
    """内存倒排表；``rows`` 传入时只用这些行（单测不走 MySQL）。"""

    def __init__(self, rows: list[Any] | None = None) -> None:
        self._lock = threading.Lock()
        self._docs: list[dict[str, Any]] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        self._avg_len = 0.0
        self._signature: tuple[int, Any] | None = None
        if rows is not None:
            self._build(rows)
            self._signature = (len(rows), None)

    # ------------------------------------------------------------ 构建
    def _build(self, rows: list[Any]) -> None:
        docs: list[dict[str, Any]] = []
        postings: dict[str, list[tuple[int, int]]] = {}
        total_length = 0
        for row in rows:
            item = row if isinstance(row, dict) else dict(row._mapping)
            tokens = tokenize(str(item.get("text") or ""))
            term_freq: dict[str, int] = {}
            for token in tokens:
                term_freq[token] = term_freq.get(token, 0) + 1
            index = len(docs)
            docs.append(
                {
                    "chunk_id": str(item.get("id") or item.get("chunk_id") or ""),
                    "doc_id": str(item.get("doc_id") or ""),
                    "kind": str(item.get("kind") or ""),
                    "page_no": int(item.get("page_no") or 0),
                    "text": str(item.get("text") or ""),
                    "image_path": item.get("image_path"),
                    "length": len(tokens),
                    "term_freq": term_freq,
                }
            )
            total_length += len(tokens)
            for token, count in term_freq.items():
                postings.setdefault(token, []).append((index, count))

        self._docs = docs
        self._postings = postings
        self._avg_len = total_length / len(docs) if docs else 0.0

    def ensure_fresh(self) -> None:
        """chunk 行数或最新写入时间变了才重建；一次 count+max 查询足够便宜。"""
        with session_scope() as session:
            total, latest = session.execute(
                select(func.count(), func.max(Chunk.created_at)).select_from(Chunk)
            ).one()
            signature = (int(total or 0), latest)
            if signature == self._signature:
                return
            rows = session.execute(
                select(
                    Chunk.id,
                    Chunk.doc_id,
                    Chunk.kind,
                    Chunk.page_no,
                    Chunk.text,
                    Chunk.image_path,
                )
            ).all()
        with self._lock:
            self._build(list(rows))
            self._signature = signature
        log_event(logger, "bm25_index_built", docs=len(self._docs))

    # ------------------------------------------------------------ 检索
    def search(self, query: str, top_k: int, doc_id: str | None = None) -> list[dict[str, Any]]:
        tokens = list(dict.fromkeys(tokenize(query)))
        if not tokens or not self._docs or top_k <= 0:
            return []
        total_docs = len(self._docs)
        average = self._avg_len or 1.0
        scores: dict[int, float] = {}
        for token in tokens:
            posting = self._postings.get(token)
            if not posting:
                continue
            idf = math.log(1 + (total_docs - len(posting) + 0.5) / (len(posting) + 0.5))
            for index, term_freq in posting:
                doc = self._docs[index]
                if doc_id and doc["doc_id"] != doc_id:
                    continue
                denominator = term_freq + K1 * (1 - B + B * doc["length"] / average)
                scores[index] = scores.get(index, 0.0) + idf * term_freq * (K1 + 1) / denominator

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:top_k]
        return [{**self._docs[index], "score": score} for index, score in ranked]


_index: BM25Index | None = None
_index_lock = threading.Lock()


def get_index() -> BM25Index:
    global _index
    if _index is None:
        with _index_lock:
            if _index is None:
                _index = BM25Index()
    return _index


def search_bm25(query: str, top_k: int, doc_id: str | None = None) -> list[dict[str, Any]]:
    index = get_index()
    index.ensure_fresh()
    return index.search(query, top_k, doc_id)


def close_index() -> None:
    """测试或进程退出时清空，避免跨用例串数据。"""
    global _index
    with _index_lock:
        _index = None
