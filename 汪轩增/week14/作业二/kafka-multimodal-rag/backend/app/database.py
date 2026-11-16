"""SQLAlchemy 数据库连接。

开发环境用 SQLite：开启 WAL + busy timeout，让 API 进程和 worker 进程可以并发读写。
"""

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings

settings = get_settings()
settings.ensure_dirs()

_is_sqlite = settings.database_url_resolved.startswith("sqlite")

engine = create_engine(
    settings.database_url_resolved,
    connect_args={"check_same_thread": False, "timeout": 30} if _is_sqlite else {},
    pool_pre_ping=True,
)

if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI 依赖：每个请求一个 session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """建表（幂等）。"""
    from . import models  # noqa: F401  确保模型已注册

    Base.metadata.create_all(engine)


def sqlite_db_file() -> Path | None:
    if not settings.database_url_resolved.startswith("sqlite:///"):
        return None
    return Path(settings.database_url_resolved[len("sqlite:///"):])
