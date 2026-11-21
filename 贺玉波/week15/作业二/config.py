"""openai-agents 版运行时配置（全部来自环境变量 / .env）。"""

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

    mock: bool = False

    # 与 LangGraph 版错开端口，方便两套同时跑
    sentiment_port: int = 8201
    response_port: int = 8202
    backend_port: int = 8200
    sentiment_url: str = "http://127.0.0.1:8201"
    response_url: str = "http://127.0.0.1:8202"
    backend_url: str = "http://127.0.0.1:8200"

    @property
    def use_mock(self) -> bool:
        return self.mock or not self.deepseek_api_key.strip()


settings = Settings()
