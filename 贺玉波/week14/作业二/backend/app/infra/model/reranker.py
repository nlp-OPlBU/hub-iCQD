"""交叉编码器重排序（bge-reranker-v2-m3）。

RRF 只看名次，名次本身并不代表「和问题真的相关」；交叉编码器把（问题, 候选正文）
拼成一对送入模型逐条打分，才能把「提到 RAG 这个词」和「真正回答了问题」区分开。

设计取舍：
- 懒加载 + 进程内单例：不检索时不占显存；
- 加载失败（没下载、没显存、依赖缺失）只记一次，之后直接返回 None 让上层退回 RRF 顺序；
- GPU 上用 fp16，CPU 上用 fp32，实测 m3 在 512 长度下约 2-3GB 显存。
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import get_settings, resolve_path
from app.core.logging import get_logger, log_event
from app.infra.model.embedding import resolve_device

logger = get_logger("app.infra.reranker")

_tokenizer: Any = None
_model: Any = None
_device: str = ""
# 加载失败后不再重试：每条 query 都重新加载会把检索拖成十几秒
_failed: str | None = None
_lock = threading.Lock()


def _load() -> bool:
    """返回是否可用；失败时记录原因，后续调用直接短路。"""
    global _tokenizer, _model, _device, _failed
    if _model is not None:
        return True
    if _failed is not None:
        return False
    with _lock:
        if _model is not None:
            return True
        if _failed is not None:
            return False
        settings = get_settings().retrieval
        try:
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
        except ImportError as exc:  # pragma: no cover - 依赖缺失属部署问题
            _failed = f"缺少 transformers/torch: {exc}"
            log_event(logger, "rerank_disabled", level=30, error=_failed)
            return False

        # 允许写本地目录（切换 GPU 实例时不必重新下载），HF id 原样传给 from_pretrained
        model_id = settings.rerank_model
        local = resolve_path(model_id)
        if local.is_dir():
            model_id = str(local)
        device = resolve_device(settings.rerank_device)
        try:
            cache_dir = str(get_settings().embed.cache_path)
            tokenizer = AutoTokenizer.from_pretrained(model_id, cache_dir=cache_dir)
            dtype = torch.float16 if device.startswith("cuda") else torch.float32
            model = AutoModelForSequenceClassification.from_pretrained(
                model_id, torch_dtype=dtype, cache_dir=cache_dir
            )
            model.to(device)
            model.eval()
        except Exception as exc:
            _failed = f"加载重排序模型失败（{model_id}）: {exc}"
            log_event(logger, "rerank_disabled", level=30, error=_failed)
            return False

        _tokenizer, _model, _device = tokenizer, model, device
        log_event(logger, "rerank_model_loaded", model=model_id, device=device)
        return True


def available() -> bool:
    return _load()


def score(query: str, texts: list[str]) -> list[float] | None:
    """返回每条候选的相关性分数（0-1）。不可用时返回 None，不抛异常。"""
    if not texts:
        return []
    if not _load():
        return None
    settings = get_settings().retrieval
    try:
        import torch

        scores: list[float] = []
        with torch.inference_mode():
            for start in range(0, len(texts), settings.rerank_batch_size):
                batch = texts[start : start + settings.rerank_batch_size]
                inputs = _tokenizer(
                    [[query, text] for text in batch],
                    padding=True,
                    truncation=True,
                    max_length=settings.rerank_max_length,
                    return_tensors="pt",
                ).to(_device)
                logits = _model(**inputs).logits.view(-1).float()
                scores.extend(float(value) for value in torch.sigmoid(logits).cpu().tolist())
        return scores
    except Exception as exc:  # 推理异常（显存碎片、输入过长）不该拖垮问答
        log_event(logger, "rerank_failed", level=30, error=str(exc))
        return None


def is_loaded() -> bool:
    return _model is not None


def close_model() -> None:
    global _tokenizer, _model, _failed
    with _lock:
        if _model is None:
            return
        _tokenizer = None
        _model = None
        _failed = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass
