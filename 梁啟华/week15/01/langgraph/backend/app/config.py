"""Application configuration loaded from environment variables / .env."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All runtime configuration lives here; no secrets are hard-coded."""

    app_name: str = "心语 · LangGraph 情感支持助手"
    app_version: str = "1.0.0"
    log_level: str = "INFO"

    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    llm_temperature: float = 0.3
    llm_timeout_seconds: float = 60.0
    llm_max_tokens: int = 600

    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_llm_configured(self) -> bool:
        return bool(self.deepseek_api_key.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
