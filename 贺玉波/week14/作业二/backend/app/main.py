"""FastAPI 应用装配：路由、中间件、静态资源。

接口层只做适配，业务在 services / pipeline。
"""

from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import errors, middleware
from app.api.v1 import health
from app.api.v1 import router as api_v1_router
from app.core import metrics
from app.core.config import PROJECT_ROOT, get_settings
from app.core.lifespan import lifespan
from app.core.logging import setup_logging
from app.infra.cache.redis_client import read_metrics_snapshots


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    settings.ensure_dirs()

    app = FastAPI(
        title="Week14 多模态 RAG",
        version=settings.version,
        description="PDF 入库 → 解析 → 检索 → 图文问答，全链路节点延迟可观测",
        lifespan=lifespan,
        docs_url="/docs" if settings.app_env != "prod" else None,
        redoc_url=None,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    errors.register_exception_handlers(app)
    middleware.install(app)

    app.include_router(health.router)
    app.include_router(api_v1_router, prefix="/api/v1")

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        """Prometheus 抓取口：HTTP 计数/延迟 + 各节点 span 直方图。

        api 进程自己的指标，加上 ingest-worker 经 Redis 上报的快照——
        worker 才产生 ingest.* 与 Kafka lag，不合并的话这部分会恒为 0。
        """
        remote: list[dict] = []
        try:
            remote = read_metrics_snapshots()
        except Exception:  # Redis 不可用不影响本进程指标输出
            remote = []
        return Response(
            content=metrics.render(remote), media_type="text/plain; version=0.0.4"
        )

    # 解析产物中的图片走这里，模型返回的 Markdown 路径固定为 /static/images/{doc_id}/xxx.png
    app.mount("/static/images", StaticFiles(directory=str(settings.storage.images)), name="images")

    # 前端构建产物存在时由本进程托管，实现单端口交付
    dist = PROJECT_ROOT / "frontend" / "dist"
    if (dist / "index.html").exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
