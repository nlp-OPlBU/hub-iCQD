"""RRF 融合的纯逻辑单测：不依赖 Milvus / 模型。"""

from __future__ import annotations

from app.infra.vector.milvus_client import _hits
from app.services.retrieval_service import rrf_fuse


def _hit(chunk_id: str, **extra) -> dict:
    return {"chunk_id": chunk_id, **extra}


def test_rrf_prefers_item_in_both_channels() -> None:
    text = [_hit("a"), _hit("b"), _hit("c")]
    image = [_hit("img-1"), _hit("b")]
    fused = rrf_fuse([("text", text), ("image", image)], k=60)
    # b 在两路都出现，名次分别 2 与 2，叠加速度最快
    assert fused[0]["chunk_id"] == "b"
    assert fused[0]["sources"] == ["text", "image"]


def test_rrf_score_matches_formula() -> None:
    fused = rrf_fuse([("text", [_hit("a"), _hit("b")])], k=60)
    assert fused[0]["score"] == 1 / 61
    assert fused[1]["score"] == 1 / 62


def test_rrf_keeps_first_seen_payload() -> None:
    text = [_hit("a", text="正文", page_no=3, kind="text")]
    image = [_hit("a", image_path="/x.png")]
    fused = rrf_fuse([("text", text), ("image", image)], k=60)
    assert fused[0]["text"] == "正文"
    assert fused[0]["page_no"] == 3
    assert fused[0]["image_path"] == "/x.png"


def test_rrf_empty_input() -> None:
    assert rrf_fuse([]) == []


def test_rrf_keeps_raw_scores_per_channel() -> None:
    """三路分数不同量纲，RRF 只累加名次，但原始分要留着给调试页看。"""
    fused = rrf_fuse(
        [
            ("text", [_hit("a", text="正文", score=0.60)]),
            ("bm25", [_hit("a", score=4.2)]),
            ("image", [_hit("i", image_path="/x.png", caption="架构图", score=0.49)]),
        ],
        k=60,
    )
    by_id = {row["chunk_id"]: row for row in fused}
    assert by_id["a"]["raw_scores"] == {"text": 0.60, "bm25": 4.2}
    assert by_id["a"]["sources"] == ["text", "bm25"]
    assert by_id["i"]["caption"] == "架构图"
    assert by_id["i"]["kind"] == "image"


def test_hits_reads_chunk_id_from_lite_engine() -> None:
    """Lite 引擎把主键放在 chunk_id，不放在 id；否则 chunk_id 全是 None 会被融合成一条。"""
    raw = [
        [
            {"chunk_id": "a-0001", "distance": 0.31, "entity": {"doc_id": "a", "kind": "paragraph"}},
            {"id": "a-0002", "distance": 0.29, "entity": {"doc_id": "a", "kind": "paragraph"}},
        ]
    ]
    hits = _hits(raw)
    assert [row["chunk_id"] for row in hits] == ["a-0001", "a-0002"]
    assert hits[0]["score"] == 0.31
