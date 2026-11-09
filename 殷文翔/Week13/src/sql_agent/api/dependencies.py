"""应用工作流依赖和轻量只读数据库检查。"""

import logging
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Protocol, cast

from fastapi import Request
from starlette.concurrency import run_in_threadpool

from ..models import WorkflowOutput
from .errors import APIError


logger = logging.getLogger(__name__)


class QueryWorkflow(Protocol):
    async def run(self, question: str) -> WorkflowOutput: ...


async def get_workflow(request: Request) -> QueryWorkflow:
    workflow = getattr(request.app.state, "workflow", None)
    if workflow is None:
        raise APIError(503, "service_unavailable", "查询服务尚未就绪。")
    return cast(QueryWorkflow, workflow)


def check_database(db_path: Path) -> None:
    """每次新建只读连接，避免连接池掩盖数据库文件已不可访问的情况。"""
    uri = f"{db_path.resolve().as_uri()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as connection:
        connection.execute("PRAGMA query_only = ON")
        connection.execute("SELECT 1").fetchone()


async def ensure_database_available(request: Request) -> None:
    db_path = getattr(request.app.state, "db_path", None)
    if db_path is None:
        raise APIError(503, "service_unavailable", "查询服务尚未就绪。")
    try:
        await run_in_threadpool(check_database, db_path)
    except (sqlite3.Error, OSError):
        logger.exception("request_id=%s database unavailable", request.state.request_id)
        raise APIError(503, "service_unavailable", "数据库暂不可用。") from None
