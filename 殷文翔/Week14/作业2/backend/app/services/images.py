"""Decode raster images, retain originals, and prepare bounded vision inputs."""
from __future__ import annotations

import base64
import hashlib
import io
import math
import os
import warnings
from pathlib import Path, PurePosixPath

from PIL import Image, ImageOps, UnidentifiedImageError

from app.api.errors import ApiError
from app.core.config import Settings
from app.schemas.chat import ImageRead

MEDIA = {'PNG': 'image/png', 'JPEG': 'image/jpeg', 'WEBP': 'image/webp'}


def resource_path(settings: Settings, key: str) -> Path:
    part = PurePosixPath(key)
    if not key or part.is_absolute() or '..' in part.parts or '\\' in key:
        raise ApiError(404, 'IMAGE_NOT_FOUND', '图片不存在')
    path = (settings.data_dir / key).resolve()
    if not path.is_relative_to(settings.data_dir) or not path.is_file():
        raise ApiError(404, 'IMAGE_NOT_FOUND', '图片不存在')
    return path


def decode_image(data: bytes, settings: Settings):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                media = MEDIA.get(image.format)
                if not media or getattr(image, 'n_frames', 1) != 1:
                    raise ApiError(415, 'INVALID_IMAGE', '仅支持静态 PNG、JPEG、WebP 图片')
                if image.width * image.height > settings.image_max_pixels:
                    raise ApiError(413, 'IMAGE_TOO_LARGE', '图片像素超过限制')
                image.load()
                return ImageOps.exif_transpose(image).convert('RGB'), media, image.size
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError,
            Image.DecompressionBombWarning):
        raise ApiError(415, 'INVALID_IMAGE', '图片无法解码或像素超过安全限制') from None


def inspect_image(data: bytes, settings: Settings) -> dict:
    image, media, (width, height) = decode_image(data, settings)
    image.close()
    return dict(media_type=media, width=width, height=height, size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest())


def image_read(row) -> ImageRead:
    return ImageRead(id=row.id, kind=row.kind, url=f'/api/images/{row.id}/content',
                     media_type=row.media_type, size_bytes=row.size_bytes, width=row.width,
                     height=row.height, document_id=row.document_id, version=row.version, page=row.page)


def save_original(settings: Settings, image_id: str, data: bytes) -> str:
    directory = settings.data_dir / 'uploads' / 'chat-images'
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / image_id
    with path.open('xb') as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())
    if os.name == 'posix':
        descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return path.relative_to(settings.data_dir).as_posix()


def vision_image(settings: Settings, row, *, token_limit: int) -> tuple[dict, int, int]:
    """Estimate at least one token per 28x28 patch plus framing; verify actual usage too.

    This conservative input policy targets the configured Qwen3.5 model. A different
    vision processor needs separate server validation, even if its API is compatible.
    """
    path = resource_path(settings, row.storage_key)
    if path.stat().st_size > settings.image_max_file_bytes:
        raise ApiError(413, 'IMAGE_TOO_LARGE', '图片文件超过限制')
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != row.sha256:
        raise ApiError(503, 'IMAGE_CHANGED', '持久化图片校验失败')
    image, _, _ = decode_image(data, settings)
    try:
        image.thumbnail((settings.image_max_edge, settings.image_max_edge))
        def cost():
            return math.ceil(image.width / 28) * math.ceil(image.height / 28) + 32
        while cost() > token_limit and min(image.size) > 1:
            factor = min(.9, math.sqrt(max(1, token_limit - 32) / (cost() - 32)))
            image = image.resize((max(1, int(image.width * factor)),
                                  max(1, int(image.height * factor))))
        if cost() > token_limit:
            raise ApiError(413, 'IMAGE_BUDGET_EXCEEDED', '图片 token 预算不足')
        output = io.BytesIO()
        image.save(output, format='JPEG', quality=85)
        encoded = output.getvalue()
        part = {'type': 'image_url', 'image_url': {
            'url': 'data:image/jpeg;base64,' + base64.b64encode(encoded).decode('ascii')}}
        return part, len(encoded), cost()
    finally:
        image.close()
