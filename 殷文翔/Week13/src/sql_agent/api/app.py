"""应用工厂：python -m uvicorn sql_agent.api.app:create_app --factory。"""

import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import ExitStack, asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request, Response
from fastapi.staticfiles import StaticFiles
from sqlalchemy import create_engine
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..db.catalog import SchemaCatalog, make_readonly_url
from ..db.executor import SQLExecutor
from ..db.validator import SQLValidator
from ..logging_config import configure_logging
from ..workflow import SQLAgentWorkflow
from .errors import error_response, register_exception_handlers
from .routes import health, queries, web


logger = logging.getLogger(__name__)


def _create_workflow(resources: ExitStack) -> SQLAgentWorkflow:
    # 先登记引擎清理，再解析目录，构造中途失败也能释放连接。
    catalog_engine = create_engine(make_readonly_url(settings.db_path))
    resources.callback(catalog_engine.dispose)
    catalog = SchemaCatalog(settings.db_path, engine=catalog_engine)
    validator_engine = create_engine(make_readonly_url(settings.db_path))
    resources.callback(validator_engine.dispose)
    validator = SQLValidator(settings.db_path, engine=validator_engine)
    return SQLAgentWorkflow(
        catalog=catalog, validator=validator, executor=SQLExecutor(settings.db_path),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    resources = ExitStack()
    try:
        try:
            app.state.workflow = await run_in_threadpool(_create_workflow, resources)
            app.state.db_path = settings.db_path.resolve()
        except Exception:
            logger.exception("API startup failed")
            raise
        logger.info("API ready")
        yield
    finally:
        app.state.workflow = None
        app.state.db_path = None
        await run_in_threadpool(resources.close)
        logger.info("API resources closed")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Chinook SQL Agent API", version="1.0.0",
        description="只读自然语言数据库查询服务。", lifespan=lifespan,
        redoc_url=None, redirect_slashes=False,
    )
    app.state.workflow = None
    app.state.db_path = None
    register_exception_handlers(app)

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # 在同一边界生成 500、请求 ID 和日志，未知异常也遵循 HTTP 契约。
            logger.exception("request_id=%s API request failed", request_id)
            response = error_response(500, "internal_error", "服务内部错误，请稍后重试。")
        response.headers["X-Request-ID"] = request_id
        logger.info(
            "request_id=%s method=%s path=%s status=%d elapsed=%.3fs",
            request_id, request.method, request.url.path,
            response.status_code, time.perf_counter() - started,
        )
        return response

    app.include_router(health.router)
    app.include_router(queries.router)
    app.include_router(web.router)
    app.mount("/static", StaticFiles(directory=web.STATIC_DIR), name="static")
    return app
