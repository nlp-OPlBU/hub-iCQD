"""FastAPI 应用入口。

启动：
    cd backend
    uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .api import chat, documents
from .config import get_settings
from .database import init_db
from .services.kafka_producer import close_producer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)

settings = get_settings()
# 静态目录必须在 mount 之前存在
settings.ensure_dirs()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield
    close_producer()


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(
    documents.router,
    prefix=f"{settings.api_prefix}/documents",
    tags=["documents"],
)
app.include_router(chat.router, prefix=settings.api_prefix, tags=["chat"])

# MinerU 解析出的图片：/static/processed/<doc_uuid>/.../images/xxx.jpg
app.mount(
    f"{settings.static_prefix}/processed",
    StaticFiles(directory=str(settings.processed_path)),
    name="processed",
)


@app.get("/")
def root():
    return {
        "service": settings.app_name,
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "kafka_topic": settings.kafka_parse_topic,
        "milvus_collection": settings.milvus_collection,
        "llm_model": settings.llm_model,
    }


@app.get("/api/config")
def public_config():
    """前端可以读取的公开配置。"""
    return {
        "api_prefix": settings.api_prefix,
        "static_prefix": settings.static_prefix,
        "max_upload_size_mb": settings.max_upload_size_mb,
        "max_user_images": settings.max_user_images,
        "chat_top_k": settings.chat_top_k,
    }
