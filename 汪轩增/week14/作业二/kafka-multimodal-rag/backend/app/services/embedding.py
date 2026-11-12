"""向量编码服务：BGE（文本检索）+ jina-clip-v2（图文跨模态检索）。

模型采用懒加载：只有真正上传/问答时才占用显存/内存。
"""

import logging
import os
import tempfile
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


class EmbeddingService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        os.environ.setdefault("HF_ENDPOINT", self.settings.hf_endpoint)

        self._model_lock = threading.Lock()
        self._encode_lock = threading.RLock()
        self._bge = None
        self._clip = None

    # ---------------- 模型加载 ----------------
    @property
    def bge_model(self):
        if self._bge is None:
            with self._model_lock:
                if self._bge is None:
                    from sentence_transformers import SentenceTransformer

                    logger.info("Loading BGE model: %s", self.settings.bge_model_path)
                    self._bge = SentenceTransformer(self.settings.bge_model_path)
                    logger.info("BGE model loaded")
        return self._bge

    @property
    def clip_model(self):
        if self._clip is None:
            with self._model_lock:
                if self._clip is None:
                    from sentence_transformers import SentenceTransformer

                    logger.info("Loading CLIP model: %s", self.settings.clip_model_path)
                    self._clip = SentenceTransformer(
                        self.settings.clip_model_path,
                        trust_remote_code=True,
                        truncate_dim=self.settings.clip_dim,
                    )
                    logger.info("CLIP model loaded")
        return self._clip

    # ---------------- 编码 ----------------
    def encode_text_bge(self, text: str) -> list[float]:
        text = text.strip() or " "
        with self._encode_lock:
            vector = self.bge_model.encode(text, normalize_embeddings=True)
        return _to_list(vector)

    def encode_text_clip(self, text: str) -> list[float]:
        text = text.strip() or " "
        with self._encode_lock:
            vector = self.clip_model.encode([text], normalize_embeddings=True)[0]
        return _to_list(vector)

    def encode_image_path(self, image_path: str | Path) -> list[float]:
        path = Path(image_path)
        with self._encode_lock:
            vector = self.clip_model.encode([str(path)], normalize_embeddings=True)[0]
        return _to_list(vector)

    def encode_image_pil(self, image: Image.Image) -> list[float]:
        """用户上传的图片转成临时文件再交给 CLIP 编码。"""
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
                tmp_path = Path(tmp.name)
            image.convert("RGB").save(tmp_path, format="PNG")
            return self.encode_image_path(tmp_path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def zero_image_vector(self) -> list[float]:
        return [0.0] * self.settings.clip_dim


def _to_list(vector) -> list[float]:
    return np.asarray(vector, dtype=np.float32).reshape(-1).tolist()
