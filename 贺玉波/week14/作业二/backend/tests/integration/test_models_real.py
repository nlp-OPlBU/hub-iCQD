"""真实模型冒烟测试（需要下载权重，默认被 ``-m 'not gpu'`` 排除）。

手动执行：``pytest -m gpu tests/integration/test_models_real.py -s``
"""

from __future__ import annotations

import pytest
from PIL import Image


@pytest.mark.gpu
def test_text_embedding_dim() -> None:
    from app.infra.model import embedding

    vectors = embedding.embed_texts(["多模态检索系统", "向量数据库"])
    assert len(vectors) == 2
    assert all(len(vector) == 1024 for vector in vectors)
    norm = sum(value * value for value in vectors[0]) ** 0.5
    assert abs(norm - 1.0) < 1e-3, "输出应已归一化"


@pytest.mark.gpu
def test_clip_both_modalities(tmp_path) -> None:
    from app.infra.model import clip

    path = tmp_path / "demo.png"
    Image.new("RGB", (64, 64), (30, 60, 90)).save(path)

    images = clip.embed_images([path])
    texts = clip.embed_texts(["一张蓝色图片"])
    assert images[0] is not None and len(images[0]) == 512
    assert len(texts[0]) == 512
    # 同空间，点积即余弦；只要数值合理即可
    assert -1.0 <= sum(a * b for a, b in zip(images[0], texts[0])) <= 1.0
