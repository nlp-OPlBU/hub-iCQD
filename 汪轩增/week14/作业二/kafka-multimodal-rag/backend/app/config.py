"""全局配置：全部通过环境变量 / backend/.env 覆盖。"""

from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/ 目录，所有相对路径都以它为基准，避免受启动目录影响
BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BACKEND_DIR / ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---------- 基础 ----------
    app_name: str = "Kafka 多模态 RAG 知识库"
    api_prefix: str = "/api"
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    public_base_url: str = "http://127.0.0.1:8000"
    static_prefix: str = "/static"

    # ---------- 存储 ----------
    upload_dir: str = "data/uploads"
    processed_dir: str = "data/processed"
    database_url: str = "sqlite:///./data/app.db"
    max_upload_size_mb: int = 100

    # ---------- Kafka ----------
    kafka_bootstrap_servers: str = "localhost:9092"
    kafka_parse_topic: str = "document.parse"
    kafka_dlq_topic: str = "document.parse.dlq"
    kafka_group_id: str = "document-parser"

    # ---------- MinerU ----------
    mineru_cmd: str = "mineru"
    mineru_backend: str = "vlm-http-client"
    mineru_server_url: str = "http://127.0.0.1:30000"
    mineru_timeout_seconds: int = 900
    mineru_extra_args: str = ""

    # ---------- 向量模型 ----------
    bge_model_path: str = "/root/autodl-tmp/models/BAAI/bge-small-zh-v1.5"
    clip_model_path: str = "/root/autodl-tmp/models/jinaai/jina-clip-v2"
    bge_dim: int = 512
    clip_dim: int = 1024
    hf_endpoint: str = "https://hf-mirror.com"

    # ---------- Milvus ----------
    milvus_uri: str = "http://localhost:19530"
    milvus_token: str = ""
    milvus_collection: str = "multimodal_rag"

    # ---------- LLM ----------
    llm_api_key: str = ""
    llm_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_model: str = "qwen-vl-max"
    llm_temperature: float = 0.2

    # ---------- 检索 / 问答 ----------
    chat_top_k: int = 5
    max_user_images: int = 4
    max_images_to_llm: int = 4
    max_image_bytes_mb: int = 10
    chunk_size: int = 800

    # ---------------- 派生属性 ----------------
    @property
    def upload_path(self) -> Path:
        return self._resolve_path(self.upload_dir)

    @property
    def processed_path(self) -> Path:
        return self._resolve_path(self.processed_dir)

    @property
    def static_processed_prefix(self) -> str:
        return f"{self.static_prefix.rstrip('/')}/processed"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def kafka_servers_list(self) -> list[str]:
        return [s.strip() for s in self.kafka_bootstrap_servers.split(",") if s.strip()]

    @property
    def database_url_resolved(self) -> str:
        """把 sqlite 相对路径解析成基于 backend/ 的绝对路径。"""
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            raw = self.database_url[len(prefix):]
            path = Path(raw)
            if not path.is_absolute():
                path = (BACKEND_DIR / path).resolve()
            # Windows 下 Path.as_posix() 形如 D:/xxx，可被 sqlite URL 正确识别
            return f"{prefix}{path.as_posix()}"
        return self.database_url

    def absolute_static_url(self, relative_path: str) -> str:
        """把 /static/processed/... 转成浏览器可访问的绝对地址。"""
        if not relative_path:
            return relative_path
        if not relative_path.startswith("/"):
            relative_path = "/" + relative_path
        base = self.public_base_url.rstrip("/")
        return f"{base}{relative_path}" if base else relative_path

    def static_url_to_local(self, static_url: str) -> Path | None:
        """把 /static/processed/xxx 反解成本地文件，越界或不存在返回 None。"""
        prefix = self.static_processed_prefix + "/"
        if not static_url.startswith(prefix):
            return None
        rel = unquote(static_url[len(prefix):])
        try:
            candidate = (self.processed_path / rel).resolve()
            candidate.relative_to(self.processed_path.resolve())
        except (ValueError, OSError):
            return None
        return candidate if candidate.is_file() else None

    def ensure_dirs(self) -> None:
        self.upload_path.mkdir(parents=True, exist_ok=True)
        self.processed_path.mkdir(parents=True, exist_ok=True)
        if self.database_url_resolved.startswith("sqlite:///"):
            db_file = Path(self.database_url_resolved[len("sqlite:///"):])
            db_file.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _resolve_path(value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (BACKEND_DIR / path).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
