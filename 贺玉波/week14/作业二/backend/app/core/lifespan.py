"""应用生命周期：启动预热与优雅关闭。

依赖不可用时只告警，不阻塞进程启动；真实状态由 /ready 暴露，避免"起不来"与"不可用"混为一谈。
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from app.core.config import get_settings
from app.core.logging import get_logger, log_event, setup_logging
from app.infra.cache.redis_client import close_redis, ping_redis
from app.infra.db.engine import dispose_engine, ping_mysql
from app.infra.mq.kafka_client import close_producer
from app.infra.vector.milvus_client import close_milvus, ensure_collections

logger = get_logger("app.lifespan")


async def _warmup(name: str, fn, *args) -> None:
    try:
        detail = await asyncio.to_thread(fn, *args)
        log_event(logger, "warmup_ok", dependency=name, detail=detail)
    except Exception as exc:
        log_event(logger, "warmup_failed", level=logging.WARNING, dependency=name, error=str(exc))


async def _preload_models() -> None:
    """后台预加载检索用的两个编码器。

    模型是懒加载的，不预热的话进程起来后第一次检索要等 11-18 秒；
    放到后台任务里加载，/ready 能立刻通过，用户点进来时模型刚好就绪。
    """
    from app.infra.model import clip, embedding, reranker

    settings = get_settings()
    await _warmup("embedding", embedding.embed_query, "预热")
    if settings.retrieval.rerank_enabled:
        await _warmup("reranker", reranker.available)
    await _warmup("clip", clip.embed_texts, ["预热"])


# 持有任务引用，避免事件循环把后台预热任务回收
_background: set[asyncio.Task[None]] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _background.add(task)
    task.add_done_callback(_background.discard)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    setup_logging(settings.log_level)
    settings.ensure_dirs()
    log_event(logger, "app_start", app=settings.app_name, version=settings.version, env=settings.app_env)

    await _warmup("mysql", ping_mysql)
    await _warmup("redis", ping_redis)
    await _warmup("milvus", ensure_collections)
    _spawn(_preload_models())

    try:
        yield
    finally:
        close_redis()
        close_producer()
        close_milvus()
        dispose_engine()
        log_event(logger, "app_stop")
