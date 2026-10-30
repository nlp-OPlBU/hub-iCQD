"""运行配置：优先读取环境变量，其次读取同目录下的 .env 文件。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"


def _load_env_file(path: Path) -> None:
    """极简 .env 解析，避免为一个小项目引入额外依赖。"""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # 环境变量优先于 .env
        os.environ.setdefault(key, value)


_load_env_file(ENV_FILE)


def _parse_models(raw: str) -> dict[str, str]:
    """格式：id|显示名称,id2|显示名称2，省略显示名时直接用 id。"""
    models: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        model_id, _, label = item.partition("|")
        model_id = model_id.strip()
        if model_id:
            models[model_id] = (label.strip() or model_id)
    return models


@dataclass
class Settings:
    base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"))
    api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "").strip())
    models: dict[str, str] = field(
        default_factory=lambda: _parse_models(
            os.getenv("MODELS", "ds-flash-4.1|DS Flash 4.1,qwen-vl-max|Qwen-VL-Max")
        )
    )
    default_model: str = field(default_factory=lambda: os.getenv("DEFAULT_MODEL", "").strip())
    timeout: float = field(default_factory=lambda: float(os.getenv("REQUEST_TIMEOUT", "120")))
    json_mode: bool = field(default_factory=lambda: os.getenv("JSON_MODE", "0").lower() in {"1", "true", "yes"})
    user_agent: str = field(
        default_factory=lambda: os.getenv(
            "HTTP_USER_AGENT",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        )
    )
    max_upload_mb: float = field(default_factory=lambda: float(os.getenv("MAX_UPLOAD_MB", "12")))
    max_image_side: int = field(default_factory=lambda: int(os.getenv("MAX_IMAGE_SIDE", "1280")))
    host: str = field(default_factory=lambda: os.getenv("HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.getenv("PORT", "8000")))

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and bool(self.base_url)

    @property
    def resolved_default_model(self) -> str:
        if self.default_model and self.default_model in self.models:
            return self.default_model
        return next(iter(self.models), "")

    @property
    def base_host(self) -> str:
        """只暴露网关域名，便于前端展示状态，不泄露 Key。"""
        try:
            from urllib.parse import urlparse

            return urlparse(self.base_url).netloc or self.base_url
        except Exception:
            return self.base_url


settings = Settings()
