"""重排序排序/门控与同页图片约束的纯逻辑单测：不加载模型。"""

from __future__ import annotations

from app.services.retrieval_service import attach_images_by_page, order_by_rerank


def _hit(chunk_id: str, score: float, **extra) -> dict:
    return {"chunk_id": chunk_id, "score": score, **extra}


def test_order_by_rerank_sorts_by_rerank_score() -> None:
    items = [_hit("a", 0.03), _hit("b", 0.02)]
    ordered = order_by_rerank(items, [0.62, 0.90], ratio=0.3, abs_min=0.05)
    assert [row["chunk_id"] for row in ordered] == ["b", "a"]
    assert ordered[0]["rerank_score"] == 0.90


def test_order_by_rerank_drops_noise_by_ratio() -> None:
    # 最高分 0.95，相对阈值 0.285；0.02 明显是噪声
    ordered = order_by_rerank([_hit("a", 0.1), _hit("b", 0.1)], [0.95, 0.02], ratio=0.3, abs_min=0.05)
    assert [row["chunk_id"] for row in ordered] == ["a"]


def test_order_by_rerank_drops_everything_when_all_irrelevant() -> None:
    # 全都很低时相对阈值也低，靠绝对下限兜底
    ordered = order_by_rerank([_hit("a", 0.1), _hit("b", 0.1)], [0.04, 0.03], ratio=0.3, abs_min=0.05)
    assert ordered == []


def test_order_by_rerank_falls_back_to_rrf_order() -> None:
    items = [_hit("low", 0.01), _hit("high", 0.03)]
    ordered = order_by_rerank(items, None, ratio=0.3, abs_min=0.05)
    assert [row["chunk_id"] for row in ordered] == ["high", "low"]
    assert all(row.get("rerank_score") is None for row in ordered)


def test_attach_images_keeps_only_pages_with_text() -> None:
    text_hits = [_hit("t", 0.80, doc_id="d", page_no=3)]
    image_hits = [
        _hit("img-ok", 0.40, doc_id="d", page_no=3, image_path="/d/p003_01.png"),
        _hit("img-other", 0.45, doc_id="d", page_no=9, image_path="/d/p009_01.png"),
    ]
    kept = attach_images_by_page(text_hits, image_hits)
    assert [row["chunk_id"] for row in kept] == ["img-ok"]
    # 与所属页同分，按页折叠时会紧跟在那一页后面
    assert kept[0]["score"] == 0.80


def test_attach_images_keeps_one_per_page() -> None:
    """同一页多张图只留最相关的一张，别让它占掉最终名额。"""
    text_hits = [_hit("t", 0.80, doc_id="d", page_no=3)]
    image_hits = [
        _hit("weak", 0.30, doc_id="d", page_no=3, image_path="/d/p003_01.png"),
        _hit("strong", 0.55, doc_id="d", page_no=3, image_path="/d/p003_02.png"),
    ]
    kept = attach_images_by_page(text_hits, image_hits)
    assert [row["chunk_id"] for row in kept] == ["strong"]


def test_attach_images_without_text_returns_empty() -> None:
    assert attach_images_by_page([], [_hit("img", 0.4, doc_id="d", page_no=3, image_path="/x.png")]) == []
