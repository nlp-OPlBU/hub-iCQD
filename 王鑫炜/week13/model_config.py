"""集中读取服务端模型配置。默认 DeepSeek，密钥不进入网页响应或对象 repr。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values

PROJECT_DIR = Path(__file__).resolve().parent
DEEPSEEK_MODEL = "deepseek-flash"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    api_key: str = field(repr=False)
    base_url: str
    model: str
    mode: str

    @property
    def provider_name(self) -> str:
        return "DeepSeek" if self.provider == "deepseek" else "OpenAI 兼容服务"

    @property
    def key_variable(self) -> str:
        return "DEEPSEEK_API_KEY" if self.provider == "deepseek" else "OPENAI_API_KEY"

    @property
    def ready(self) -> bool:
        placeholders = {"your-api-key", "your_api_key", "your-deepseek-api-key",
                        "填入你的api key", "你的api key", "你的服务密钥", "请填写你的密钥"}
        return bool(self.model and self.api_key and self.api_key.lower() not in placeholders)

    @property
    def setup_hint(self) -> str:
        return f"请在 week13/.env 中填写 {self.key_variable}，然后重启网页服务"


def get_model_settings(env_path: Path | None = None,
                       environ: Mapping[str, str] | None = None) -> ModelSettings:
    """环境变量优先于 .env；不会把文件配置写回进程环境，便于测试和隔离。"""
    path = env_path if env_path is not None else PROJECT_DIR / ".env"
    file_values = dotenv_values(path) if path.is_file() else {}
    values = {**{key: value or "" for key, value in file_values.items()},
              **(os.environ if environ is None else environ)}
    provider = values.get("SQL_AGENT_PROVIDER", "deepseek").strip().lower()
    if provider not in {"deepseek", "openai"}:
        raise ValueError("SQL_AGENT_PROVIDER 必须是 deepseek 或 openai")
    if provider == "deepseek":
        # 不把其他服务商的 OPENAI_API_KEY 自动发给 DeepSeek。
        key = values.get("DEEPSEEK_API_KEY", "").strip()
        base_url = values.get("DEEPSEEK_BASE_URL", "").strip() or DEEPSEEK_BASE_URL
        model = values.get("DEEPSEEK_MODEL", "").strip() or DEEPSEEK_MODEL
    else:
        key = values.get("OPENAI_API_KEY", "").strip()
        base_url = values.get("OPENAI_BASE_URL", "").strip() or "https://api.openai.com/v1"
        model = values.get("OPENAI_MODEL", "").strip()
    mode = values.get("SQL_AGENT_MODE", "live").strip().lower()
    if mode not in {"live", "demo", "auto"}:
        raise ValueError("SQL_AGENT_MODE 必须是 live、demo 或 auto")
    if mode == "auto":
        mode = "live" if provider == "deepseek" or key or model else "demo"
    return ModelSettings(provider, key, base_url, model, mode)
