"""SQLAlchemy 引擎与会话。

进程内单例连接池；``ping_mysql`` 供 /ready 使用。
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings = get_settings().mysql
        _engine = create_engine(
            settings.dsn(),
            pool_pre_ping=True,
            pool_size=settings.pool_size,
            pool_timeout=settings.pool_timeout,
            pool_recycle=settings.pool_recycle,
            echo=settings.echo,
            future=True,
        )
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_mysql() -> None:
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1"))


def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _session_factory = None
