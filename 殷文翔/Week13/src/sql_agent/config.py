"""运行配置。API key 只从环境变量读取。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    db_path: Path = PROJECT_ROOT / "ref" / "chinook.db"
    primary_model: str = "deepseek-flash"
    summary_model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    max_repair_attempts: int = 3
    max_result_rows: int = 200
    max_summary_rows: int = 50
    max_cell_chars: int = 2000
    query_timeout_seconds: float = 5.0

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.getenv("SQL_AGENT_DB", str(cls.db_path)))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        return cls(
            db_path=db_path,
            primary_model=os.getenv("SQL_AGENT_MODEL", cls.primary_model),
            summary_model=os.getenv("SQL_AGENT_SUMMARY_MODEL", cls.summary_model),
            base_url=os.getenv("DEEPSEEK_URL", cls.base_url),
            max_repair_attempts=_env_int(
                "SQL_AGENT_MAX_REPAIR_ATTEMPTS", cls.max_repair_attempts
            ),
            max_result_rows=_env_int("SQL_AGENT_MAX_ROWS", cls.max_result_rows),
            max_summary_rows=_env_int(
                "SQL_AGENT_MAX_SUMMARY_ROWS", cls.max_summary_rows
            ),
            max_cell_chars=_env_int("SQL_AGENT_MAX_CELL_CHARS", cls.max_cell_chars),
            query_timeout_seconds=_env_float(
                "SQL_AGENT_QUERY_TIMEOUT", cls.query_timeout_seconds
            ),
        )


settings = Settings.from_env()
