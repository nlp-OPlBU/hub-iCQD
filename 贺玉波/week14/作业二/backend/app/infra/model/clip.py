"""图像向量模型（Chinese-CLIP ViT-B/16，512 维，图文同空间）。

只提供两个能力：图片 → 向量（入库）、文本 → 向量（以文搜图）。
与文本 embedding 一样懒加载、显存不足自动降级 CPU。
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.infra.model.embedding import ModelUnavailable, resolve_device

logger = get_logger("app.infra.clip")

_model: Any = None
_processor: Any = None
_lock = threading.Lock()


def _load() -> tuple[Any, Any]:
    global _model, _processor
    if _model is not None and _processor is not None:
        return _model, _processor
    with _lock:
        if _model is None or _processor is None:
            settings = get_settings().clip
            try:
                import torch
                from transformers import ChineseCLIPModel, ChineseCLIPProcessor
            except ImportError as exc:  # pragma: no cover
                raise ModelUnavailable("缺少 transformers / torch，请安装依赖") from exc

            device = resolve_device(settings.device)
            try:
                model = ChineseCLIPModel.from_pretrained(settings.model, cache_dir=str(settings.cache_path))
                processor = ChineseCLIPProcessor.from_pretrained(settings.model, cache_dir=str(settings.cache_path))
            except Exception as exc:
                raise ModelUnavailable(f"加载图像向量模型失败（{settings.model}）: {exc}") from exc
            model.to(device).eval()
            _model, _processor = model, processor
            log_event(logger, "clip_model_loaded", model=settings.model, device=device, dim=settings.dim)
    return _model, _processor


def _pooler(output: Any) -> Any:
    """transformers 5.x 的 get_*_features 返回 BaseModelOutputWithPooling（投影在 pooler_output），
    旧版直接返回张量；两种都收敛成特征张量。
    """
    return getattr(output, "pooler_output", output)


def _normalize(features: Any) -> list[list[float]]:
    import torch
    import torch.nn.functional as functional

    normalized = functional.normalize(features, dim=-1)
    return [[float(value) for value in row] for row in normalized.cpu().tolist()]


def embed_images(paths: list[Path]) -> list[list[float] | None]:
    """逐张编码；打不开或过大的图片返回 None，由调用方跳过。"""
    if not paths:
        return []
    import torch
    from PIL import Image

    settings = get_settings().clip
    model, processor = _load()
    results: list[list[float] | None] = []
    max_bytes = settings.max_image_mb * 1024 * 1024

    for start in range(0, len(paths), settings.batch_size):
        batch_paths = paths[start : start + settings.batch_size]
        images = []
        valid: list[int] = []
        for offset, path in enumerate(batch_paths):
            try:
                if path.stat().st_size > max_bytes:
                    log_event(logger, "clip_image_too_large", level=30, image=str(path), size=path.stat().st_size)
                    continue
                with Image.open(path) as img:
                    images.append(img.convert("RGB"))
                valid.append(offset)
            except Exception as exc:
                log_event(logger, "clip_image_broken", level=30, image=str(path), error=str(exc))
        vectors: list[list[float]] = []
        if images:
            inputs = processor(images=images, return_tensors="pt").to(model.device)
            with torch.inference_mode():
                vectors = _normalize(_pooler(model.get_image_features(**inputs)))
        cursor = 0
        for offset in range(len(batch_paths)):
            if offset in valid and cursor < len(vectors):
                results.append(vectors[cursor])
                cursor += 1
            else:
                results.append(None)
    return results


def embed_texts(texts: list[str]) -> list[list[float]]:
    """文本侧编码，用于“以文搜图”。"""
    if not texts:
        return []
    import torch

    model, processor = _load()
    inputs = processor(text=texts, padding=True, truncation=True, return_tensors="pt").to(model.device)
    with torch.inference_mode():
        return _normalize(_pooler(model.get_text_features(**inputs)))


def is_loaded() -> bool:
    return _model is not None


def close_model() -> None:
    global _model, _processor
    with _lock:
        _model, _processor = None, None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass
