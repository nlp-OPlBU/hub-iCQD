"""索引 + 检索集成测试。

用确定性假编码器替代模型（模型边界，不是中间件），验证的是
Milvus 写入、按 doc_id 清理、过滤检索、RRF 融合这几段真实逻辑。
真实模型另有 ``test_embedding_real.py``（gpu 标记）负责。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.infra.model import clip as clip_module
from app.infra.model import embedding as embedding_module
from app.infra.vector import milvus_client as mv
from app.pipeline import index as index_pipeline
from app.services import retrieval_service

DOC_ID = "idx-test-doc"


def _text_vector(text: str) -> list[float]:
    """按首字编码成 one-hot，保证不同文本相似度可比且可预期。"""
    vector = [0.0] * 1024
    vector[ord(text[0]) % 1024] = 1.0
    return vector


def _image_vector(path: Path) -> list[float]:
    vector = [0.0] * 512
    vector[len(path.name) % 512] = 1.0
    return vector


@pytest.fixture()
def fake_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embedding_module, "embed_texts", lambda texts, is_query=False: [_text_vector(t) for t in texts])
    monkeypatch.setattr(embedding_module, "embed_query", lambda text: _text_vector(text))
    monkeypatch.setattr(clip_module, "embed_images", lambda paths: [_image_vector(p) for p in paths])
    monkeypatch.setattr(clip_module, "embed_texts", lambda texts: [[0.0] * 511 + [1.0] for _ in texts])


@pytest.fixture()
def sample_images(tmp_path: Path) -> list[Path]:
    """文件名要符合 MinerU 的 p<页码>_<序号>.png 约定，图片才能挂到对应页。"""
    paths = []
    for name in ("p001_1.png", "p002_02.png"):
        path = tmp_path / name
        Image.new("RGB", (32, 32), (200, 120, 60)).save(path)
        paths.append(path)
    return paths


def _chunks() -> list[dict]:
    return [
        {"id": f"{DOC_ID}-0000", "doc_id": DOC_ID, "idx": 0, "kind": "text", "page_no": 1, "text": "多模态检索系统"},
        {"id": f"{DOC_ID}-0001", "doc_id": DOC_ID, "idx": 1, "kind": "text", "page_no": 2, "text": "向量数据库选型"},
    ]


@pytest.mark.integration
def test_index_then_search(milvus_ready, fake_models, sample_images: list[Path]) -> None:
    text_rows, image_rows = index_pipeline.index_document(DOC_ID, _chunks(), sample_images)
    assert (text_rows, image_rows) == (2, 2)

    hits, took_ms = retrieval_service.search("多模态", top_k=5, with_images=True, doc_id=DOC_ID)
    assert hits, "检索结果不应为空"
    assert hits[0]["chunk_id"] == f"{DOC_ID}-0000"
    assert hits[0]["sources"] == ["text"]
    assert any(hit["kind"] == "image" for hit in hits), "图片通道应有命中"
    assert took_ms >= 0


@pytest.mark.integration
def test_reindex_is_idempotent(milvus_ready, fake_models, sample_images: list[Path]) -> None:
    index_pipeline.index_document(DOC_ID, _chunks(), sample_images)
    index_pipeline.index_document(DOC_ID, _chunks(), sample_images)
    assert mv.count_vectors(DOC_ID) == (2, 2)


@pytest.mark.integration
def test_drop_document_clears_vectors(milvus_ready, fake_models, sample_images: list[Path]) -> None:
    index_pipeline.index_document(DOC_ID, _chunks(), sample_images)
    index_pipeline.drop_document(DOC_ID)
    assert mv.count_vectors(DOC_ID) == (0, 0)
