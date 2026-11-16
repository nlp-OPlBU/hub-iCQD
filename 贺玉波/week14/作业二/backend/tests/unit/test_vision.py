"""命中图片 → 模型可见的 data URL：只编码合法、可读、大小可控的图。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.infra.model import vision

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


@pytest.fixture()
def image_dir() -> Path:
    path = get_settings().storage.images / ("d" * 32)
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_encodes_png_under_image_root(image_dir: Path) -> None:
    (image_dir / "a.png").write_bytes(PNG)
    url = f"/static/images/{image_dir.name}/a.png"
    data = vision.to_data_url(url)
    assert data is not None and data.startswith("data:image/png;base64,")


def test_blocks_path_traversal() -> None:
    assert vision.to_data_url("/static/images/../../etc/passwd") is None


def test_skips_unsupported_format(image_dir: Path) -> None:
    (image_dir / "a.svg").write_text("<svg/>", encoding="utf-8")
    assert vision.to_data_url(f"/static/images/{image_dir.name}/a.svg") is None


def test_skips_oversized_image(image_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (image_dir / "big.png").write_bytes(PNG)
    monkeypatch.setattr(vision, "MAX_IMAGE_BYTES", 8)
    assert vision.to_data_url(f"/static/images/{image_dir.name}/big.png") is None


def test_batch_skips_missing_and_keeps_order(image_dir: Path) -> None:
    (image_dir / "b.png").write_bytes(PNG)
    urls = [
        f"/static/images/{image_dir.name}/b.png",
        f"/static/images/{image_dir.name}/missing.png",
    ]
    encoded = vision.to_data_urls(urls)
    assert len(encoded) == 1 and encoded[0].startswith("data:image/png;base64,")
