"""文本向量模型（Qwen3-Embedding-0.6B，1024 维）。

懒加载 + 进程内单例：只有真正索引/检索时才占显存，API 进程空闲时不加载。
显存不足或无 CUDA 时自动降级到 CPU，链路不中断（只打一条 warning）。
"""

from __future__ import annotations

import threading
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ErrorCode
from app.core.logging import get_logger, log_event

logger = get_logger("app.infra.embedding")

_model: Any = None
_lock = threading.Lock()


class ModelUnavailable(RuntimeError):
    """模型无法加载：由上层转成 MODEL_UNAVAILABLE 错误码。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = ErrorCode.MODEL_UNAVAILABLE


def resolve_device(preferred: str) -> str:
    """按配置挑设备；CUDA 不可用就退回 CPU，避免整条链路因为没卡而失败。"""
    if not preferred.startswith("cuda"):
        return preferred
    try:
        import torch

        if torch.cuda.is_available():
            return preferred
    except Exception:  # torch 缺失或驱动异常都走降级
        pass
    log_event(logger, "embed_device_fallback", level=30, requested=preferred, actual="cpu")
    return "cpu"


def get_model() -> Any:
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        settings = get_settings().embed
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - 依赖缺失属部署问题
            raise ModelUnavailable("缺少 sentence-transformers，请执行 pip install -r requirements.txt") from exc

        device = resolve_device(settings.device)
        try:
            model = SentenceTransformer(
                settings.model,
                device=device,
                cache_folder=str(settings.cache_path),
            )
        except Exception as exc:
            raise ModelUnavailable(f"加载文本向量模型失败（{settings.model}）: {exc}") from exc
        model.max_seq_length = settings.max_length
        _model = model
        log_event(logger, "embed_model_loaded", model=settings.model, device=device, dim=settings.dim)
    return _model


def embed_texts(texts: list[str], *, is_query: bool = False) -> list[list[float]]:
    """批量编码。``is_query=True`` 时按 Qwen3 官方要求加指令前缀。"""
    if not texts:
        return []
    settings = get_settings().embed
    model = get_model()
    payload = texts
    if is_query:
        payload = [f"Instruct: {settings.query_instruction}\nQuery: {text}" for text in texts]
    vectors = model.encode(
        payload,
        batch_size=settings.batch_size,
        normalize_embeddings=settings.normalize,
        show_progress_bar=False,
    )
    return [[float(value) for value in vector] for vector in vectors]


def embed_query(text: str) -> list[float]:
    return embed_texts([text], is_query=True)[0]


def is_loaded() -> bool:
    return _model is not None


def close_model() -> None:
    """释放显存；进程退出或测试切换设备时调用。"""
    global _model
    with _lock:
        if _model is None:
            return
        _model = None
        try:
            import torch

            torch.cuda.empty_cache()
        except Exception:  # pragma: no cover
            pass
