"""One configuration source for API, migrations and future workers."""
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PositiveInt = Annotated[int, Field(gt=0)]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env", env_file_encoding="utf-8", extra="ignore",
        case_sensitive=False, hide_input_in_errors=True,
    )

    app_name: str = "Multimodal RAG"
    app_host: str = "127.0.0.1"
    app_port: int = Field(default=8001, ge=1, le=65535)
    app_debug: bool = False
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    data_dir: Path = Path("data")
    log_dir: Path = Path("logs")
    cors_origins: list[str] = []

    mysql_host: str = "localhost"
    mysql_port: int = Field(default=3306, ge=1, le=65535)
    mysql_database: str = "multimodal_rag"
    mysql_user: str = "rag_app"
    mysql_password: SecretStr = SecretStr("")
    mysql_connect_timeout_seconds: PositiveInt = 3
    mysql_pool_size: PositiveInt = 5
    mysql_pool_recycle_seconds: PositiveInt = 1800

    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_pdf_topic: str = "multimodal-rag.pdf.parse"
    kafka_failed_topic: str = "multimodal-rag.pdf.failed"
    kafka_consumer_group: str = "multimodal-rag-pdf-workers"
    kafka_max_poll_interval_ms: PositiveInt = 3600000
    redis_host: str = "localhost"
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_db: int = Field(default=0, ge=0)
    redis_password: SecretStr = SecretStr("")
    redis_key_prefix: str = "multimodal-rag:"
    redis_cache_ttl_seconds: PositiveInt = 60
    es_url: str = "http://localhost:9200"
    es_index: str = "multimodal-rag-chunks-v1"
    es_username: str = ""
    es_password: SecretStr = SecretStr("")
    es_ca_certs: Path | None = None
    es_analyzer: str = "standard"

    llm_base_url: str = "http://127.0.0.1:8100/v1"
    llm_api_key: SecretStr = SecretStr("EMPTY")
    llm_model: str = "rag-vlm"
    llm_vision_enabled: bool = False
    llm_timeout_seconds: PositiveInt = 120
    mineru_command: str = "mineru-kit"
    mineru_working_dir: Path = PROJECT_ROOT
    mineru_home: Path = Path("mineru")
    mineru_config: Path = Path("mineru/config.yaml")
    mineru_tier: str = "advanced"
    mineru_timeout_seconds: PositiveInt = 1800
    mineru_max_concurrency: PositiveInt = 1

    retrieval_mode: Literal["bm25", "hybrid"] = "bm25"
    embedding_base_url: str = "http://127.0.0.1:8102/v1"
    embedding_api_key: SecretStr = SecretStr("EMPTY")
    embedding_model: str = "rag-embedding"
    embedding_dimensions: PositiveInt = 2048
    rerank_base_url: str = "http://127.0.0.1:8103"
    rerank_api_key: SecretStr = SecretStr("EMPTY")
    rerank_model: str = "rag-reranker"
    rerank_enabled: bool = False

    upload_max_files: PositiveInt = 10
    upload_max_file_bytes: PositiveInt = 50 * 1024 * 1024
    upload_max_request_bytes: PositiveInt = 200 * 1024 * 1024
    zip_max_entries: PositiveInt = 10000
    zip_max_entry_bytes: PositiveInt = 100 * 1024 * 1024
    zip_max_uncompressed_bytes: PositiveInt = 1024 * 1024 * 1024
    zip_max_compression_ratio: PositiveInt = 100
    task_max_retries: int = Field(default=3, ge=0)
    task_retry_base_seconds: PositiveInt = 5
    task_retry_max_seconds: PositiveInt = 300
    task_lease_seconds: PositiveInt = 120
    task_heartbeat_seconds: PositiveInt = 30
    outbox_max_retries: int = Field(default=10, ge=0)
    outbox_lease_seconds: PositiveInt = 60
    image_max_upload_files: PositiveInt = 4
    image_max_file_bytes: PositiveInt = 10 * 1024 * 1024
    image_max_pixels: PositiveInt = 20000000
    image_max_edge: PositiveInt = 2048
    image_max_context_count: PositiveInt = 8
    image_max_context_bytes: PositiveInt = 20 * 1024 * 1024
    image_token_budget: PositiveInt = 4096
    rag_context_token_budget: PositiveInt = 12000
    llm_max_output_tokens: PositiveInt = 2048

    @model_validator(mode="before")
    @classmethod
    def empty_optional_paths(cls, values):
        if isinstance(values, dict) and values.get("es_ca_certs") == "":
            values["es_ca_certs"] = None
        return values

    @model_validator(mode="after")
    def validate_and_resolve(self):
        for name in ("data_dir", "log_dir", "mineru_working_dir", "mineru_home", "mineru_config", "es_ca_certs"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, (PROJECT_ROOT / value).resolve() if not value.is_absolute() else value.resolve())
        if self.task_heartbeat_seconds * 2 >= self.task_lease_seconds:
            raise ValueError("TASK_LEASE_SECONDS must exceed twice TASK_HEARTBEAT_SECONDS")
        if self.upload_max_file_bytes > self.upload_max_request_bytes:
            raise ValueError("UPLOAD_MAX_FILE_BYTES exceeds UPLOAD_MAX_REQUEST_BYTES")
        if self.zip_max_entry_bytes > self.zip_max_uncompressed_bytes:
            raise ValueError("ZIP_MAX_ENTRY_BYTES exceeds ZIP_MAX_UNCOMPRESSED_BYTES")
        if self.task_retry_base_seconds > self.task_retry_max_seconds:
            raise ValueError("TASK_RETRY_BASE_SECONDS exceeds TASK_RETRY_MAX_SECONDS")
        if self.image_max_upload_files > self.image_max_context_count:
            raise ValueError("IMAGE_MAX_UPLOAD_FILES exceeds IMAGE_MAX_CONTEXT_COUNT")
        if self.image_token_budget >= self.rag_context_token_budget:
            raise ValueError("Image token budget must leave room for text context")
        if self.retrieval_mode == "hybrid" and not self.embedding_model.strip():
            raise ValueError("Hybrid retrieval requires an embedding model")
        return self

    @property
    def mysql_url(self) -> URL:
        # URL.create handles passwords containing @, /, %, etc. Never log this URL.
        return URL.create("mysql+pymysql", username=self.mysql_user,
                          password=self.mysql_password.get_secret_value(), host=self.mysql_host,
                          port=self.mysql_port, database=self.mysql_database,
                          query={"charset": "utf8mb4"})


@lru_cache
def get_settings() -> Settings:
    return Settings()
