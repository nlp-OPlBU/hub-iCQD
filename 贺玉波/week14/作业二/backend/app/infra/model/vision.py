"""把命中图片转成模型能读的 ``image_url``。

检索侧 CLIP 只负责“以文搜图”，但生成侧要真正看懂图，必须把图片本体交给模型。
本地文件云端模型访问不到，所以统一编码成 ``data:image/...;base64,``。

限制：单张 ≤4MB、只认常见位图格式；超限或格式不支持时跳过（只打日志），
不影响文本问答——宁可少一张图，也不要整轮失败。
"""

from __future__ import annotations

import base64
from pathlib import Path

from app.core.config import get_settings
from app.core.logging import get_logger, log_event

logger = get_logger("app.infra.vision")

URL_PREFIX = "/static/images/"
MAX_IMAGE_BYTES = 4 * 1024 * 1024
_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


def local_path(url: str) -> Path | None:
    """把 ``/static/images/<doc_id>/<name>`` 映射为磁盘路径，并挡住路径穿越。"""
    if not url.startswith(URL_PREFIX):
        return None
    base = get_settings().storage.images.resolve()
    target = (base / url[len(URL_PREFIX) :].lstrip("/")).resolve()
    if not target.is_relative_to(base):
        return None
    return target


def to_data_url(url: str) -> str | None:
    path = local_path(url)
    if path is None or not path.is_file():
        return None
    mime = _MIME.get(path.suffix.lower())
    if mime is None:
        log_event(logger, "vision_unsupported_format", level=30, image=url)
        return None
    size = path.stat().st_size
    if size > MAX_IMAGE_BYTES:
        log_event(logger, "vision_image_too_large", level=30, image=url, bytes=size)
        return None
    return f"data:{mime};base64," + base64.b64encode(path.read_bytes()).decode("ascii")


def to_data_urls(urls: list[str]) -> list[str]:
    """批量编码，跳过失败项；顺序与入参一致。"""
    encoded: list[str] = []
    for url in urls:
        data = to_data_url(url)
        if data:
            encoded.append(data)
    return encoded
