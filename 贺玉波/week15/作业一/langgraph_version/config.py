"""LangGraph 版运行时配置（全部来自环境变量 / .env）。"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout: float = 30.0
    llm_max_retries: int = 1

    # MOCK=1 时完全不调用模型，便于无 key / 断网演示
    mock: bool = False

    # 三个进程的端口与地址
    sentiment_port: int = 8101
    response_port: int = 8102
    backend_port: int = 8000
    sentiment_url: str = "http://127.0.0.1:8101"
    response_url: str = "http://127.0.0.1:8102"
    backend_url: str = "http://127.0.0.1:8000"

    @property
    def use_mock(self) -> bool:
        """没有配置 key 时自动降级到本地规则，保证作业可演示。"""
        return self.mock or not self.deepseek_api_key.strip()


settings = Settings()
