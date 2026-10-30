"""运行：python -m uvicorn fire_inspection.app:app --host 0.0.0.0 --port 8000"""

import asyncio
import io
import time
import warnings
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import APIConnectionError, APIStatusError, APITimeoutError
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from common import Settings
from .schemas import classify
from .vision import QwenVision

STATIC = Path(__file__).resolve().parent / "static"
MAX_UPLOAD = 10 * 1024 * 1024
MAX_PIXELS = 20_000_000


def normalize_image(raw: bytes) -> bytes:
    """按文件内容解码；限制像素；处理手机照片旋转；重新编码去掉 EXIF。"""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValueError("仅支持 JPEG、PNG、WebP；HEIC 请先转换为 JPEG。")
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError("图片不能超过 2000 万像素，请缩小后上传。")
                if min(original.size) < 64:
                    raise ValueError("图片尺寸过小，请上传至少 64×64 像素的照片。")
                original.load()
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((1600, 1600))
                output = io.BytesIO()
                image.save(output, format="JPEG", quality=88)
                return output.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError,
            Image.DecompressionBombWarning) as exc:
        raise ValueError("图片无法解码或尺寸超限，请使用有效的 JPEG、PNG、WebP。") from exc


def create_app(analyzer=None, settings=None) -> FastAPI:
    settings = settings or Settings.load()
    app = FastAPI(title="楼道消防巡检", version="1.0.0")
    app.state.analyzer = analyzer or QwenVision(settings)
    # 教学服务同时最多处理 2 张照片，避免多次点击造成请求堆积。
    app.state.slots = asyncio.Semaphore(2)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/config")
    async def config():
        try:
            settings.validate_online()
            configured = True
        except ValueError:
            configured = False
        return {"configured": configured, "model": settings.vision_model,
                "max_upload_mb": 10, "request_timeout_seconds": settings.timeout + 5}

    @app.post("/api/inspect")
    async def inspect_image(image: UploadFile = File(...)):
        started = time.monotonic()
        try:
            # 不依赖前端 MIME / 扩展名，不存储原始照片。
            raw = await image.read(MAX_UPLOAD + 1)
        finally:
            await image.close()
        if not raw:
            raise HTTPException(400, "请选择一张照片。")
        if len(raw) > MAX_UPLOAD:
            raise HTTPException(413, "照片不能超过 10 MB。")
        try:
            jpeg = await run_in_threadpool(normalize_image, raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        if app.state.slots.locked():
            raise HTTPException(429, "当前识别请求较多，请稍后重试。")
        async with app.state.slots:
            try:
                observations = await asyncio.wait_for(
                    app.state.analyzer.analyze(jpeg), timeout=settings.timeout + 2)
                assessment = classify(observations)
            except (APITimeoutError, asyncio.TimeoutError):
                raise HTTPException(504, "模型识别超时，请稍后重试。") from None
            except APIConnectionError:
                raise HTTPException(502, "无法连接模型服务，请检查网络和接口地址。") from None
            except APIStatusError as exc:
                message = {401: "模型认证失败，请检查后端 API Key。", 403: "模型访问被拒绝，请检查模型权限。",
                           404: "模型或接口不存在，请检查模型名及 Base URL。", 429: "模型限流或额度不足，请稍后重试。"}
                raise HTTPException(502, message.get(exc.status_code, "模型服务调用失败，请检查配置。")) from None
            except ValidationError:
                raise HTTPException(502, "模型返回的视觉事实格式无效，请重新识别；本次未生成风险结论。") from None
            except ValueError as exc:
                # 配置错误与模型文本错误均不向前端暴露供应商请求体。
                if not settings.api_key or not settings.base_url or "{" in settings.base_url:
                    raise HTTPException(503, "请先在后端 .env 中配置 API Key 和兼容接口地址。") from None
                raise HTTPException(502, "模型响应或配置无效，请核对后端配置后重试。") from None
        return {**assessment.model_dump(), "model": settings.vision_model,
                "elapsed_ms": round((time.monotonic() - started) * 1000)}

    return app


app = create_app()
