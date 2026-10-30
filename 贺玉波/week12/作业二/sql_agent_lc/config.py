"""运行配置：模型、数据库路径与限额。

密钥只从环境变量读取，支持项目根目录下的 .env 文件（不读取命令行参数，便于运行）。
优先级：已存在的环境变量 > .env 文件。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path) -> None:
    """极简 .env 解析：KEY=VALUE，忽略空行与 # 注释，不覆盖已有环境变量。"""

    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


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
    db_path: Path = PROJECT_ROOT / "data" / "chinook.db"
    model: str = "deepseek-flash"
    base_url: str = "https://api.deepseek.com"
    request_timeout: float = 60.0
    max_repair_attempts: int = 3
    max_result_rows: int = 200
    max_summary_rows: int = 50
    max_cell_chars: int = 2000
    query_timeout_seconds: float = 5.0

    @property
    def api_key(self) -> str:
        key = os.getenv("DEEPSEEK_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
        if not key:
            raise RuntimeError(
                "未配置 API key：请设置环境变量 DEEPSEEK_API_KEY，或在项目根目录 .env 中填写。"
            )
        return key

    @classmethod
    def from_env(cls) -> "Settings":
        db_path = Path(os.getenv("SQL_AGENT_DB", str(cls.db_path)))
        if not db_path.is_absolute():
            db_path = PROJECT_ROOT / db_path

        return cls(
            db_path=db_path,
            model=os.getenv("SQL_AGENT_MODEL", cls.model),
            base_url=os.getenv("DEEPSEEK_BASE_URL", cls.base_url),
            request_timeout=_env_float(
                "SQL_AGENT_REQUEST_TIMEOUT", cls.request_timeout
            ),
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


load_dotenv(PROJECT_ROOT / ".env")
settings = Settings.from_env()
