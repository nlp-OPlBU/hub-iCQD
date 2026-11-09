from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator


PROJECT_DIR = Path(__file__).resolve().parents[2]
# 数据库位于 langchain-agent 的同级 data 目录，不能依赖调用命令时的 cwd。
WORKSPACE_DIR = PROJECT_DIR.parent


class Settings(BaseModel):
    """应用启动配置；所有外部输入在创建工作流前集中校验。"""

    provider: Literal["deepseek", "openai"] = "deepseek"
    model: str
    api_key: str
    base_url: str | None = None
    db_path: Path = WORKSPACE_DIR / "data" / "chinook.db"
    max_rewrites: int = Field(default=2, ge=0, le=5)
    max_result_rows: int = Field(default=100, ge=1, le=1000)
    max_progress_steps: int = Field(default=1_000_000, ge=1_000)
    timeout: float = Field(default=60, gt=0)
    max_retries: int = Field(default=1, ge=0, le=5)

    @field_validator("db_path")
    @classmethod
    def validate_db_path(cls, value: Path) -> Path:
        path = value.expanduser()
        if not path.is_absolute():
            path = (PROJECT_DIR / path).resolve()
        if not path.is_file():
            raise ValueError("数据库文件不存在或不可读")
        return path

    @classmethod
    def from_env(cls) -> "Settings":
        """从项目本地 .env 和进程环境构建配置，不向日志暴露密钥。"""
        load_dotenv(PROJECT_DIR / ".env")
        provider = os.getenv("SQL_AGENT_PROVIDER", "deepseek").strip().lower()
        model = os.getenv("SQL_AGENT_MODEL", "").strip()
        if not model:
            raise ValueError("必须设置 SQL_AGENT_MODEL")

        if provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            base_url = os.getenv("SQL_AGENT_BASE_URL", "https://api.deepseek.com").strip()
        elif provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            base_url = None
        else:
            raise ValueError("SQL_AGENT_PROVIDER 只能是 deepseek 或 openai")
        if not api_key:
            raise ValueError(f"缺少 {provider} Provider 的 API Key")

        db_value = os.getenv("SQL_AGENT_DB_PATH")
        return cls(
            provider=provider,
            model=model,
            api_key=api_key,
            base_url=base_url,
            db_path=Path(db_value) if db_value else WORKSPACE_DIR / "data" / "chinook.db",
            max_rewrites=int(os.getenv("SQL_AGENT_MAX_REWRITES", "2")),
            max_result_rows=int(os.getenv("SQL_AGENT_MAX_RESULT_ROWS", "100")),
            max_progress_steps=int(os.getenv("SQL_AGENT_MAX_PROGRESS_STEPS", "1000000")),
            timeout=float(os.getenv("SQL_AGENT_MODEL_TIMEOUT_SECONDS", "60")),
            max_retries=int(os.getenv("SQL_AGENT_MODEL_MAX_RETRIES", "1")),
        )
