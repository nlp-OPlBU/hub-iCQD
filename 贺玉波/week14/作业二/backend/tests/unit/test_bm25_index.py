"""BM25 词法召回的纯逻辑单测：注入行数据，不依赖 MySQL。"""

from __future__ import annotations

from app.infra.vector.bm25_index import BM25Index, tokenize

ROWS = [
    {
        "id": "a",
        "doc_id": "d1",
        "kind": "paragraph",
        "page_no": 3,
        "text": "天弘基金的因子模型与组合优化",
        "image_path": None,
    },
    {
        "id": "b",
        "doc_id": "d1",
        "kind": "paragraph",
        "page_no": 5,
        "text": "检索增强生成（RAG）由检索器与生成器两部分组成",
        "image_path": None,
    },
    {
        "id": "c",
        "doc_id": "d2",
        "kind": "heading",
        "page_no": 1,
        "text": "HippoRAG 的检索流程",
        "image_path": None,
    },
]


def test_tokenize_mixes_cjk_bigram_and_ascii() -> None:
    tokens = tokenize("RAG 检索增强生成")
    assert "rag" in tokens
    assert "检" in tokens and "检索" in tokens and "索" in tokens


def test_tokenize_handles_empty_text() -> None:
    assert tokenize("") == []


def test_search_puts_term_match_first() -> None:
    index = BM25Index(ROWS)
    hits = index.search("检索增强生成", 3)
    assert hits[0]["chunk_id"] == "b"
    assert hits[0]["score"] > 0
    # 只返回真正含查询词的块，而不是把所有块按顺序返回
    assert {hit["chunk_id"] for hit in hits} == {"b", "c"}


def test_search_filters_by_doc_id() -> None:
    index = BM25Index(ROWS)
    hits = index.search("检索", 5, doc_id="d2")
    assert [hit["chunk_id"] for hit in hits] == ["c"]


def test_search_without_terms_returns_empty() -> None:
    index = BM25Index(ROWS)
    assert index.search("", 3) == []
    # 全是不在语料里的英文词，倒排表命中为空
    assert index.search("zzz qqq", 3) == []


def test_build_handles_row_objects() -> None:
    """SQLAlchemy Row 用 ``_mapping`` 取值，这里用一个最小替身验证兼容。"""

    class Row:
        def __init__(self) -> None:
            self._mapping = ROWS[1]

    index = BM25Index([Row()])
    assert index.search("RAG", 1)[0]["chunk_id"] == "b"
