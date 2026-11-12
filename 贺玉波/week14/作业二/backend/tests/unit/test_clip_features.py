"""CLIP 特征输出兼容：transformers 5.x 返回 BaseModelOutputWithPooling。"""

from __future__ import annotations

from types import SimpleNamespace

from app.infra.model.clip import _pooler


def test_pooler_extracts_projected_features() -> None:
    class FakeTensor:
        pass

    projected = FakeTensor()
    output = SimpleNamespace(pooler_output=projected)
    assert _pooler(output) is projected


def test_pooler_passes_through_legacy_tensor() -> None:
    class FakeTensor:
        pass

    tensor = FakeTensor()
    assert _pooler(tensor) is tensor
