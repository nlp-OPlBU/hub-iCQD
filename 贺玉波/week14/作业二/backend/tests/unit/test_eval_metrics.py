"""检索质量指标与评测入口的纯逻辑单测。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.eval import run
from app.eval.metrics import (
    aggregate,
    hit_at_k,
    mrr,
    ndcg_at_k,
    percentile,
    recall_at_k,
)
from app.services import retrieval_service


def test_hit_at_k_respects_cutoff() -> None:
    retrieved = ["a", "b", "c"]
    assert hit_at_k(retrieved, {"c"}, 3) == 1.0
    assert hit_at_k(retrieved, {"c"}, 2) == 0.0


def test_recall_at_k_fraction_of_relevant() -> None:
    assert recall_at_k(["a", "x", "b"], {"a", "b", "c"}, 3) == pytest.approx(2 / 3)
    assert recall_at_k(["a"], set(), 3) == 0.0


def test_mrr_uses_first_relevant_rank() -> None:
    assert mrr(["x", "a"], {"a"}) == 0.5
    assert mrr(["a", "b"], {"a", "b"}) == 1.0
    assert mrr(["x"], {"a"}) == 0.0


def test_ndcg_penalises_late_relevance() -> None:
    # 相关性只有 b，但它排在第 2 名：dcg = 1/log2(3)
    assert ndcg_at_k(["a", "b"], {"b"}, 2) == pytest.approx(0.6309, abs=1e-4)
    # 相关结果都在最前面时为 1
    assert ndcg_at_k(["a", "b"], {"a", "b"}, 2) == pytest.approx(1.0)


def test_percentile_uses_nearest_rank() -> None:
    values = [10, 20, 30, 40, 50]
    assert percentile(values, 0.5) == 30
    assert percentile(values, 0.95) == 50
    assert percentile([], 0.5) == 0.0


def test_aggregate_rounds_and_handles_empty() -> None:
    assert aggregate([]) == {}
    assert aggregate([{"hit": 1.0}, {"hit": 0.0}]) == {"hit": 0.5}


def test_load_rows_skips_comments_and_blanks(tmp_path: Path) -> None:
    golden = tmp_path / "golden.jsonl"
    golden.write_text(
        '# 注释行\n\n{"query": "问题", "relevant": ["c1"]}\n',
        encoding="utf-8",
    )
    rows = run.load_rows(golden)
    assert rows == [{"query": "问题", "relevant": ["c1"]}]


def test_load_rows_rejects_incomplete_sample(tmp_path: Path) -> None:
    golden = tmp_path / "bad.jsonl"
    golden.write_text(json.dumps({"query": "问题"}) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run.load_rows(golden)


def test_evaluate_collects_metrics_and_latency(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_search(query: str, top_k: int | None = None, **_: object):
        return [{"chunk_id": "c1"}, {"chunk_id": "c2"}], 5

    monkeypatch.setattr(retrieval_service, "search", fake_search)
    result = run.evaluate(
        [{"query": "问题", "relevant": ["c1"]}], top_k=2, with_images=False
    )
    assert result["queries"] == 1
    # 只聚合数值字段：query / retrieved / miss 这类字符串不应进均值
    assert result["metrics"] == {"hit": 1.0, "recall": 1.0, "mrr": 1.0, "ndcg": 1.0}
    assert result["details"][0]["retrieved"] == ["c1", "c2"]
    assert result["details"][0]["miss"] == []
    assert result["latency_ms"]["p50"] >= 0
