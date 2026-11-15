from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", env_file=".env", extra="ignore")

    env: str = "development"
    database_path: Path = Path("data/rag.sqlite3")
    storage_path: Path = Path("data/storage")
    adapter_mode: str = "local"
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_topic: str = "rag.pdf.parse.requested"
    kafka_consumer_group: str = "rag-pdf-parser-group"
    elasticsearch_url: str = "http://localhost:9200"
    elasticsearch_username: str = ""
    elasticsearch_password: str = ""
    elasticsearch_api_key: str = ""
    elasticsearch_verify_certs: bool = True
    mineru_api_url: str = ""
    mineru_api_key: str = ""
    mineru_model_version: str = "vlm"
    mineru_poll_seconds: int = 10
    mineru_upload_timeout_seconds: int = 300
    elasticsearch_index: str = "rag_chunks"
    worker_id: str = "local-worker"
    worker_concurrency: int = 2
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-chat"
    deepseek_api_url: str = "https://api.deepseek.com/v1/chat/completions"
    max_upload_files: int = 10
    max_upload_bytes: int = 100 * 1024 * 1024
    outbox_poll_seconds: float = 2.0
    outbox_lease_seconds: int = 30
    api_key: str = ""
    rate_limit_per_minute: int = 300


@lru_cache
def get_settings() -> Settings:
    return Settings()
