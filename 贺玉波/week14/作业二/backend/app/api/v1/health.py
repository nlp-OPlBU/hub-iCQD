"""运维探针：/health 只判进程，/ready 逐项检查依赖。"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_trace_id
from app.infra.cache.redis_client import ping_redis
from app.infra.db.engine import ping_mysql
from app.infra.mq.kafka_client import ping_kafka
from app.infra.model.vllm_client import ping_vllm
from app.infra.vector.milvus_client import ping_milvus

router = APIRouter(tags=["ops"])


@router.get("/health", summary="存活探针：不查依赖")
async def health() -> dict[str, Any]:
    settings = get_settings()
    return {"status": "ok", "app": settings.app_name, "version": settings.version, "env": settings.app_env}


async def _run_sync(name: str, fn: Callable[[], Any], timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(asyncio.to_thread(fn), timeout=timeout)
        return {"ok": True, "ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    except asyncio.TimeoutError:
        return {"ok": False, "ms": int((time.perf_counter() - started) * 1000), "error": f"timeout>{timeout}s"}
    except Exception as exc:
        return {"ok": False, "ms": int((time.perf_counter() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"}


async def _run_async(name: str, fn: Callable[[], Awaitable[Any]], timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        detail = await asyncio.wait_for(fn(), timeout=timeout)
        return {"ok": True, "ms": int((time.perf_counter() - started) * 1000), "detail": detail}
    except asyncio.TimeoutError:
        return {"ok": False, "ms": int((time.perf_counter() - started) * 1000), "error": f"timeout>{timeout}s"}
    except Exception as exc:
        return {"ok": False, "ms": int((time.perf_counter() - started) * 1000), "error": f"{type(exc).__name__}: {exc}"}


@router.get("/ready", summary="就绪探针：MySQL / Redis / Kafka / Milvus / 对话模型")
async def ready() -> JSONResponse:
    settings = get_settings()
    timeout = settings.health_timeout_s
    # 云端 API 要走公网 TLS，1.5s 容易误判；本地 vLLM 保持原超时
    llm_timeout = max(timeout, 5.0) if settings.vllm.is_cloud else timeout
    checks = await asyncio.gather(
        _run_sync("mysql", ping_mysql, timeout),
        _run_sync("redis", ping_redis, timeout),
        _run_sync("kafka", ping_kafka, timeout),
        _run_sync("milvus", ping_milvus, timeout),
        _run_async("vllm", lambda: ping_vllm(timeout=llm_timeout), llm_timeout + 0.5),
    )
    names = ["mysql", "redis", "kafka", "milvus", "vllm"]
    payload: dict[str, Any] = {
        "status": "ok" if all(item["ok"] for item in checks) else "degraded",
        "checks": dict(zip(names, checks)),
        "trace_id": get_trace_id(),
    }
    return JSONResponse(status_code=200 if payload["status"] == "ok" else 503, content=payload)
