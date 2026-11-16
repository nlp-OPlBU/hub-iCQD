from collections.abc import Generator

from fastapi import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings


def create_db_engine(settings: Settings):
    return create_engine(settings.mysql_url, pool_pre_ping=True, isolation_level="REPEATABLE READ",
                         pool_size=settings.mysql_pool_size,
                         pool_recycle=settings.mysql_pool_recycle_seconds,
                         hide_parameters=True,
                         connect_args={"connect_timeout": settings.mysql_connect_timeout_seconds,
                                       "read_timeout": settings.mysql_connect_timeout_seconds,
                                       "write_timeout": settings.mysql_connect_timeout_seconds})


def session_factory(engine):
    return sessionmaker(bind=engine, expire_on_commit=False)


def get_session(request: Request) -> Generator[Session, None, None]:
    # Repositories/services explicitly choose transaction boundaries using session.begin().
    with request.app.state.session_factory() as session:
        yield session
