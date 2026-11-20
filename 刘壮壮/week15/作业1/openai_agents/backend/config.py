import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent.parent


def _resolve_api_key() -> str:
    """仅读取 DEEPSEEK_API_KEY。"""
    value = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if value and value != "your_deepseek_api_key_here":
        return value
    return ""


class Settings(BaseModel):
    """后端全局配置与模型参数配置。"""

    api_key: str
    base_url: str | None
    model_name: str
    sentiment_temperature: float = 0.0
    response_temperature: float = 0.7
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """加载 .env 并返回缓存的全局配置实例。"""
    load_dotenv(BASE_DIR / ".env")
    return Settings(
        api_key=_resolve_api_key(),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").strip() or None,
        model_name=os.getenv("OPENAI_MODEL", "deepseek-flash").strip() or "deepseek-flash",
        sentiment_temperature=float(os.getenv("SENTIMENT_TEMPERATURE", "0")),
        response_temperature=float(os.getenv("RESPONSE_TEMPERATURE", "0.7")),
        backend_host=os.getenv("BACKEND_HOST", "127.0.0.1").strip() or "127.0.0.1",
        backend_port=int(os.getenv("BACKEND_PORT", "8000")),
    )
