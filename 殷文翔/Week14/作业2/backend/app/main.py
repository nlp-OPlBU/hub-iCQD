import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import install_error_handlers
from app.api.router import api_router
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, request_id_var
from app.db.session import create_db_engine, session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        configure_logging(settings)
        engine = create_db_engine(settings)  # Lazy connection; no create_all or migration.
        application.state.engine = engine
        application.state.session_factory = session_factory(engine)
        logging.getLogger(__name__).info("API started")
        try:
            yield
        finally:
            engine.dispose()

    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan,
                          docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
    application.state.settings = settings
    if settings.cors_origins:
        application.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                                   allow_methods=["GET", "POST"],
                                   allow_headers=["Content-Type", "Idempotency-Key"],
                                   expose_headers=["X-Request-ID"])

    @application.middleware("http")
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        token = request_id_var.set(request.state.request_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request.state.request_id
            route = request.scope.get("route")
            logging.getLogger("app.access").info("%s %s status=%s duration_ms=%.1f", request.method,
                getattr(route, "path", "unmatched"), response.status_code, (time.perf_counter() - start) * 1000)
            return response
        finally:
            request_id_var.reset(token)

    install_error_handlers(application)
    application.include_router(api_router)
    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn

    config = get_settings()
    uvicorn.run("app.main:app", host=config.app_host, port=config.app_port,
                reload=config.app_debug, log_config=None, access_log=False)
