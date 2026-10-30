# -*- coding: utf-8 -*-
"""FastAPI 后端：提供 /api/analyze 接口并托管前端静态页面。

工程化要点：
- 全局限流（按客户端 IP 的滑动窗口）+ 图片 hash 短时缓存
- request_id 贯穿请求与日志，统一错误结构
- 输入边界：非空、大小、格式、可解码性
"""

import hashlib
import logging
import os
import time
import uuid
from contextvars import ContextVar
from pathlib import Path

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import rules, vision
from .guard import RateLimiter, TTLCache
from .schema import AnalyzeResult, Scene

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"
MAX_UPLOAD_BYTES = 32 * 1024 * 1024  # 官方单图上限 32 MiB

RATE_LIMIT_PER_MINUTE = int(os.getenv("RATE_LIMIT_PER_MINUTE", "20"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "600"))

logger = logging.getLogger("week12")
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

limiter = RateLimiter(limit=RATE_LIMIT_PER_MINUTE, window_seconds=60)
cache = TTLCache(ttl_seconds=CACHE_TTL_SECONDS)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def setup_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s [%(request_id)s] %(message)s")
    )
    handler.addFilter(RequestIdFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class ApiError(Exception):
    """业务异常：统一转成 {"ok": false, "error": {...}} 结构。"""

    def __init__(self, status_code: int, code: str, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error_body(code: str, message: str) -> dict:
    return {
        "ok": False,
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id_var.get(),
        },
    }


setup_logging()
app = FastAPI(title="楼道消防隐患识别", version="1.0")


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    token = request_id_var.set(request_id)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception("未处理异常 path=%s", request.url.path)
        request_id_var.reset(token)
        raise
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    response.headers["X-Request-ID"] = request_id
    if request.url.path.startswith("/api/"):
        logger.info(
            "%s %s -> %s %sms",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
        )
    request_id_var.reset(token)
    return response


@app.exception_handler(ApiError)
async def handle_api_error(request: Request, exc: ApiError):
    return JSONResponse(
        status_code=exc.status_code, content=_error_body(exc.code, exc.message)
    )


@app.exception_handler(StarletteHTTPException)
async def handle_http_error(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(f"http_{exc.status_code}", str(exc.detail)),
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=400,
        content=_error_body("invalid_request", "请求参数不合法，请上传图片文件（字段名 file）"),
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": vision.MODEL,
        "base_url": vision.BASE_URL,
        "detail": vision.IMAGE_DETAIL,
        "key_configured": bool(vision.API_KEY),
        "rate_limit_per_minute": RATE_LIMIT_PER_MINUTE,
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
    }


@app.post("/api/analyze", response_model=AnalyzeResult)
async def analyze(request: Request, file: UploadFile = File(...)):
    # 1. 限流
    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = limiter.allow(client_ip)
    if not allowed:
        raise ApiError(429, "rate_limited", f"请求过于频繁，请 {retry_after} 秒后再试")

    # 2. 输入边界校验
    raw = await file.read()
    if not raw:
        raise ApiError(400, "empty_file", "没有读取到图片内容")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ApiError(413, "file_too_large", "图片超过 32MB，请压缩后再上传")

    try:
        jpeg = await run_in_threadpool(vision.prepare_image, raw)
    except ValueError as exc:
        raise ApiError(400, "invalid_image", str(exc)) from exc

    # 3. 同一张图短时缓存，避免重复计费
    digest = hashlib.sha256(jpeg).hexdigest()
    outcome = cache.get(digest)
    cached = outcome is not None
    if cached:
        logger.info("命中缓存 digest=%s", digest[:12])
    else:
        try:
            outcome = await run_in_threadpool(vision.analyze_prepared, jpeg)
        except RuntimeError as exc:
            raise ApiError(500, "config_error", str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            logger.warning("模型调用失败：%s", exc)
            raise ApiError(502, "model_error", "模型调用失败，请稍后重试") from exc
        cache.set(digest, outcome)

    data = outcome.get("data") or {}
    if not data:
        logger.warning(
            "解析失败 finish_reason=%s tokens=%s",
            outcome.get("finish_reason"),
            outcome.get("tokens"),
        )
        raise ApiError(502, "parse_error", "模型返回内容无法解析为 JSON，请重新拍摄后重试")

    # 4. 输出归一 + 规则校正
    is_corridor = data.get("is_corridor", True)
    is_corridor = is_corridor if isinstance(is_corridor, bool) else str(is_corridor).lower() != "false"
    hazards = rules.normalize_hazards(data.get("hazards"))

    model_level = rules.normalize_level(data.get("risk_level"))
    level, corrected = rules.review(model_level, is_corridor, hazards)

    return AnalyzeResult(
        risk_level=level,
        risk_label=rules.LEVEL_LABEL.get(level, "未知"),
        scene=Scene(is_corridor=is_corridor, desc=str(data.get("scene_desc", ""))[:120]),
        hazards=hazards,
        reasons=[str(x)[:80] for x in data.get("reasons", []) if str(x).strip()][:4],
        suggestions=[str(x)[:80] for x in data.get("suggestions", []) if str(x).strip()][:4],
        confidence=_safe_float(data.get("confidence")),
        uncertain=[str(x)[:80] for x in data.get("uncertain", []) if str(x).strip()][:4],
        model_raw_level=model_level,
        corrected=corrected,
        parse_error=outcome.get("parse_error", False),
        cached=cached,
        model=outcome.get("model", ""),
        latency_ms=outcome.get("latency_ms", 0),
        tokens=outcome.get("tokens"),
        request_id=request_id_var.get(),
    )


def _safe_float(value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


# 静态页面放在最后挂载，避免把 /api 路由一起吃掉
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
