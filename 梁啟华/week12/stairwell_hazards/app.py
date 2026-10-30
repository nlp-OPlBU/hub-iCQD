"""楼道消防隐患识别 Web 应用：FastAPI 提供接口并托管前端静态页面。"""

from __future__ import annotations

import io
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from config import ROOT, settings
from llm import AnalysisError, analyze

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("stairwell")

STATIC_DIR = ROOT / "static"
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp", "image/jpg"}

app = FastAPI(title="楼道消防安全隐患识别", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict:
    return {
        "configured": settings.configured,
        "base_host": settings.base_host,
        "models": [{"id": model_id, "label": label} for model_id, label in settings.models.items()],
        "default_model": settings.resolved_default_model,
        "max_upload_mb": settings.max_upload_mb,
    }


@app.post("/api/analyze")
def analyze_image(
    file: UploadFile = File(...),
    model: str = Form(default=""),
) -> JSONResponse:
    model_id = (model or settings.resolved_default_model).strip()
    if not model_id:
        raise _error("no_model", "没有可用的模型，请检查 .env 中的 MODELS 配置。", 500)
    if model_id not in settings.models:
        raise _error("unknown_model", f"不支持的模型：{model_id}", 400)

    limit_bytes = int(settings.max_upload_mb * 1024 * 1024)
    raw = file.file.read(limit_bytes + 1)
    if not raw:
        raise _error("empty_file", "上传的文件为空，请重新选择照片。", 400)
    if len(raw) > limit_bytes:
        raise _error("too_large", f"图片超过 {settings.max_upload_mb:g} MB，请压缩后重试。", 413)
    if file.content_type not in ALLOWED_TYPES:
        raise _error("bad_type", "仅支持 JPG / PNG / WEBP / BMP 格式的图片。", 415)

    try:
        Image.open(io.BytesIO(raw)).verify()
    except Exception:
        raise _error("bad_image", "文件不是有效的图片，请重新选择。", 400)

    try:
        result = analyze(raw, model_id)
    except AnalysisError as exc:
        logger.warning("分析失败 %s: %s", exc.code, exc.message)
        raise _error(exc.code, exc.message, exc.status) from exc

    logger.info("识别完成 model=%s level=%s cost=%sms", model_id, result["risk_level"], result["elapsed_ms"])
    return JSONResponse(result)


@app.exception_handler(AnalysisError)
def _handle_analysis_error(_, exc: AnalysisError) -> JSONResponse:
    return JSONResponse({"detail": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


def _error(code: str, message: str, status: int) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message})


if __name__ == "__main__":
    import uvicorn

    print(f"楼道消防隐患识别已启动： http://{settings.host}:{settings.port}")
    if not settings.configured:
        print("提示：尚未配置 API Key，请复制 .env.example 为 .env 并填写后重启。")
    uvicorn.run(app, host=settings.host, port=settings.port)
