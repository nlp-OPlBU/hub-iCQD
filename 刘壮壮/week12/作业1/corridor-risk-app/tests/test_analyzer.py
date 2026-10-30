import base64
import io

import pytest
from PIL import Image

from backend.analyzer import (
    InvalidImageError,
    ModelServiceError,
    _extract_scene,
    prepare_image,
)


def make_image(width: int = 2400, height: int = 1200) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (width, height), color=(230, 232, 235)).save(output, "PNG")
    return output.getvalue()


def test_prepare_image_returns_resized_jpeg_data_url() -> None:
    data_url = prepare_image(make_image())
    prefix, payload = data_url.split(",", 1)

    assert prefix == "data:image/jpeg;base64"
    with Image.open(io.BytesIO(base64.b64decode(payload))) as image:
        assert image.format == "JPEG"
        assert max(image.size) <= 1600


def test_prepare_image_rejects_invalid_content() -> None:
    with pytest.raises(InvalidImageError, match="有效的图片"):
        prepare_image(b"not-an-image")


def test_model_call_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(ModelServiceError, match="DEEPSEEK_API_KEY"):
        _extract_scene("data:image/jpeg;base64,AA==")
