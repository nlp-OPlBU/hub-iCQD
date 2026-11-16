"""应用配置。

所有可变参数集中在此，业务代码里不出现魔法数字。
环境变量使用双下划线表示分组，例如 ``MYSQL__HOST``、``VLLM__TIMEOUT``。

凭据只从环境变量 / ``.env`` 读取，源码里不写默认密码；缺失时启动即失败。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote_plus

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# week14 根目录：backend/app/core/config.py -> parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def resolve_path(raw: str | Path) -> Path:
    """相对路径一律相对项目根目录，避免受进程工作目录影响。"""
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


class MySQLSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "rag"
    pool_size: int = 10
    pool_timeout: int = 5
    pool_recycle: int = 1800
    echo: bool = False

    def dsn(self, database: str | None = None) -> str:
        return (
            f"mysql+pymysql://{self.user}:{quote_plus(self.password)}"
            f"@{self.host}:{self.port}/{database or self.database}?charset=utf8mb4"
        )

    @property
    def server_dsn(self) -> str:
        """不带库名的连接串，用于建库与建表。"""
        return self.dsn("mysql")


class RedisSettings(BaseModel):
    host: str = "127.0.0.1"
    port: int = 6379
    db: int = 0
    password: str | None = None
    socket_timeout: float = 1.0
    socket_connect_timeout: float = 1.0
    chat_ttl_seconds: int = 7 * 24 * 3600
    trace_ttl_seconds: int = 24 * 3600

    @property
    def url(self) -> str:
        auth = f":{quote_plus(self.password)}@" if self.password else ""
        return f"redis://{auth}{self.host}:{self.port}/{self.db}"


class KafkaSettings(BaseModel):
    bootstrap_servers: str = "127.0.0.1:9092"
    topic: str = "doc.ingest"
    dlq_topic: str = "doc.ingest.dlq"
    group_id: str = "rag-ingest-worker-v1"
    partitions: int = 1
    retention_ms: int = 3 * 24 * 3600 * 1000
    dlq_retention_ms: int = 7 * 24 * 3600 * 1000
    max_poll_records: int = 1
    max_poll_interval_ms: int = 900_000
    auto_offset_reset: str = "earliest"
    request_timeout_ms: int = 5000


class MilvusSettings(BaseModel):
    uri: str = "data/milvus/rag.db"
    text_collection: str = "doc_text"
    image_collection: str = "doc_image"
    text_dim: int = 1024
    image_dim: int = 512
    metric_type: str = "COSINE"
    index_type: str = "FLAT"

    @property
    def is_remote(self) -> bool:
        """``http://host:port`` 表示连独立的 Milvus Lite 服务（api/worker 共享）。"""
        return self.uri.startswith(("http://", "https://", "tcp://"))

    @property
    def path(self) -> Path:
        return resolve_path(self.uri)

    @property
    def connect_uri(self) -> str:
        """本地文件模式（单进程）或远程 Lite 服务，两种都直接喂给 MilvusClient。"""
        return self.uri if self.is_remote else str(self.path)


class EmbedSettings(BaseModel):
    """文本向量模型：Qwen3-Embedding-0.6B，输出 1024 维。"""

    model: str = "Qwen/Qwen3-Embedding-0.6B"
    device: str = "cuda"  # cuda | cpu；无 GPU 时自动降级
    batch_size: int = 16
    max_length: int = 1024
    dim: int = 1024
    normalize: bool = True
    query_instruction: str = "Given a web search query, retrieve relevant passages that answer the query"
    cache_dir: str = "models"
    hf_endpoint: str = "https://hf-mirror.com"

    @property
    def cache_path(self) -> Path:
        return resolve_path(self.cache_dir)


class ClipSettings(BaseModel):
    """图像向量模型：Chinese-CLIP ViT-B/16，图文同空间 512 维。"""

    model: str = "OFA-Sys/chinese-clip-vit-base-patch16"
    device: str = "cuda"
    batch_size: int = 8
    dim: int = 512
    cache_dir: str = "models"
    max_image_mb: int = 20

    @property
    def cache_path(self) -> Path:
        return resolve_path(self.cache_dir)


class RetrievalSettings(BaseModel):
    """检索链路：向量召回 → 词法召回 → RRF → 重排序 → 分数门控。

    所有开关默认打开，但每一层失败都只降级不阻断：
    模型没下载 / 显存不够 / MySQL 读不到时退回纯向量 RRF，问答仍能照常进行。
    """

    # 召回宽度：重排序之前尽量拿宽，最终只留少数几条进上下文
    text_top_k: int = 20
    image_top_k: int = 8
    bm25_top_k: int = 20
    rrf_k: int = 60
    max_snippet_chars: int = 600

    # 重排序：交叉编码器逐对打分，只处理前 rerank_top_n 条，控制显存与延迟
    rerank_enabled: bool = True
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_device: str = "cuda"
    rerank_batch_size: int = 16
    rerank_max_length: int = 512
    rerank_top_n: int = 30

    # 分数门控：同时满足「不低于最高分的一定比例」和「不低于绝对下限」才保留
    gate_ratio: float = 0.30
    gate_abs_min: float = 0.05

    # 最终进上下文的条数（按页折叠后通常还要再少几条）
    final_top_k: int = 6

    # 短问题条件式改写：一次轻量补全，超时或失败都直接用原问题
    rewrite_enabled: bool = True
    rewrite_max_chars: int = 12
    rewrite_timeout: float = 3.0


class ChatSettings(BaseModel):
    history_token_budget: int = 1500  # 历史消息最多占多少 token
    summary_trigger_tokens: int = 3000  # 未摘要历史超过这个量就滚动摘要
    summary_keep_messages: int = 6  # 摘要后仍保留原文的最近消息数
    summary_max_chars: int = 800


class VLLMSettings(BaseModel):
    """对话模型：本地 vLLM 与云端 OpenAI 兼容 API 使用同一套客户端，只换配置。

    ``provider=local`` 走本地 vLLM（作业要求的部署形态、离线可用）；
    ``provider=cloud`` 走云端（更快更强、不占显存），此时不必启动 vLLM。
    """

    provider: Literal["local", "cloud"] = "local"
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "qwen2.5-vl-7b"
    api_key: str = ""
    max_tokens: int = 1024
    temperature: float = 0.2
    timeout: float = 120.0
    concurrency: int = 2
    max_images: int = 2  # 每轮最多把几张命中图片编码进 prompt；0 表示只给文本
    # 云端 DeepSeek 默认开启思考模式，会把 max_tokens 全用在 reasoning_content，
    # content 直接为空；RAG 问答要的是可直接展示的答案，所以默认关闭。
    thinking: Literal["enabled", "disabled"] = "disabled"

    @property
    def is_cloud(self) -> bool:
        return self.provider == "cloud"


class MinerUSettings(BaseModel):
    bin: str = "mineru"
    # MinerU 4.x 的档位：flash 走本地轻量解析（实测 14 页约 6 秒，无需 parse-server）；
    # standard/advanced 依赖 parse_server.local.mode=managed（内部起 vLLM），环境不稳时别开。
    tier: str = "flash"
    model_source: str = "modelscope"
    device: str = "cpu"
    # 1000 页在 flash 档也远超 10 分钟，超时按大文档放宽
    timeout: int = 3600
    max_pages: int = 1000
    chunk_size: int = 800
    chunk_overlap: int = 100
    max_attempts: int = 3


class StorageSettings(BaseModel):
    raw_dir: str = "data/pdf"
    parsed_dir: str = "data/parsed"
    image_dir: str = "data/images"
    max_upload_mb: int = 200
    max_files_per_request: int = 20

    @property
    def raw(self) -> Path:
        return resolve_path(self.raw_dir)

    @property
    def parsed(self) -> Path:
        return resolve_path(self.parsed_dir)

    @property
    def images(self) -> Path:
        return resolve_path(self.image_dir)


class TraceSettings(BaseModel):
    enabled: bool = True
    persist: bool = True
    sample_rate: float = 1.0
    slow_node_ms: int = 2000


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env", ".env"),
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "week14-multimodal-rag"
    version: str = "0.1.0"
    app_env: Literal["dev", "test", "prod"] = "dev"
    log_level: str = "INFO"
    api_host: str = "0.0.0.0"
    api_port: int = 8080
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    health_timeout_s: float = 1.5

    mysql: MySQLSettings = Field(default_factory=MySQLSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kafka: KafkaSettings = Field(default_factory=KafkaSettings)
    milvus: MilvusSettings = Field(default_factory=MilvusSettings)
    embed: EmbedSettings = Field(default_factory=EmbedSettings)
    clip: ClipSettings = Field(default_factory=ClipSettings)
    retrieval: RetrievalSettings = Field(default_factory=RetrievalSettings)
    chat: ChatSettings = Field(default_factory=ChatSettings)
    vllm: VLLMSettings = Field(default_factory=VLLMSettings)
    mineru: MinerUSettings = Field(default_factory=MinerUSettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    trace: TraceSettings = Field(default_factory=TraceSettings)

    @field_validator("log_level")
    @classmethod
    def _upper_level(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def _required_credentials(self) -> "Settings":
        if not self.mysql.password:
            raise ValueError("缺少数据库口令：请设置 MYSQL__PASSWORD 或写入 .env")
        # 向量维度必须与集合定义一致，否则写入时才会报错，排查成本高
        if self.embed.dim != self.milvus.text_dim:
            raise ValueError(f"EMBED__DIM={self.embed.dim} 与 MILVUS__TEXT_DIM={self.milvus.text_dim} 不一致")
        if self.clip.dim != self.milvus.image_dim:
            raise ValueError(f"CLIP__DIM={self.clip.dim} 与 MILVUS__IMAGE_DIM={self.milvus.image_dim} 不一致")
        if self.vllm.is_cloud and not self.vllm.api_key:
            raise ValueError("云端对话模型缺少密钥：请设置 VLLM__API_KEY，或把 VLLM__PROVIDER 改回 local")
        # huggingface_hub 在 import 时读一次 HF_ENDPOINT，
        # 必须赶在任何模型库被导入之前写进进程环境，否则镜像不生效。
        os.environ.setdefault("HF_ENDPOINT", self.embed.hf_endpoint)
        return self

    @property
    def is_test(self) -> bool:
        return self.app_env == "test"

    def ensure_dirs(self) -> None:
        """运行期目录一次性建好，避免各模块自行 mkdir。"""
        paths = [
            self.storage.raw,
            self.storage.parsed,
            self.storage.images,
            self.embed.cache_path,
            self.clip.cache_path,
            PROJECT_ROOT / "logs",
            PROJECT_ROOT / "logs" / "mineru",
            PROJECT_ROOT / "run",
        ]
        if not self.milvus.is_remote:
            paths.append(self.milvus.path.parent)
        for path in paths:
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """测试切换环境变量时使用。"""
    get_settings.cache_clear()


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}
