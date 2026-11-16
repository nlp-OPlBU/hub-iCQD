"""
FastAPI 主入口 - 启动后端服务（同时挂载前端页面）
"""
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
# asyncio, os 不再需要，已移除

# 确保项目根目录在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.config_loader import Config  # noqa: E402
from app.db import get_db  # noqa: E402
from app.kafka.consumer import KafkaConsumer  # noqa: E402
from app.services.pipeline import run_pipeline_sync  # noqa: E402

# ---------- 应用实例 ----------
app = FastAPI(
    title="多模态 RAG 问答系统",
    description="基于 Kafka + MinerU + ChromaDB + 多模态 LLM 的 RAG 问答系统",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

# ---------- CORS ----------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- 静态资源（解析输出的图片等） ----------
cfg_storage = Config.get_section('storage')
parsed_dir = cfg_storage['parsed_dir']
image_dir = cfg_storage['image_dir']
Path(parsed_dir).mkdir(parents=True, exist_ok=True)
Path(image_dir).mkdir(parents=True, exist_ok=True)

app.mount("/static/parsed", StaticFiles(directory=parsed_dir), name="parsed")
app.mount("/static/images", StaticFiles(directory=image_dir), name="images")

# ---------- 引入 API 路由（必须在前端静态文件之前） ----------
from app.routers.upload import router as upload_router  # noqa: E402
from app.routers.status import router as status_router  # noqa: E402
from app.routers.chat import router as chat_router  # noqa: E402

app.include_router(upload_router)
app.include_router(status_router)
app.include_router(chat_router)

# ---------- 前端静态文件（最后挂载，保证 /api/* 不被匹配） ----------
FRONTEND_DIR = PROJECT_ROOT / "frontend"
if FRONTEND_DIR.exists():
    # 挂载前端 assets 目录
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

# ---------- Kafka 消费者 ----------
kafka_consumer: KafkaConsumer = None


@app.on_event("startup")
async def on_startup():
    """启动时：初始化数据库 + 启动 Kafka 消费者"""
    global kafka_consumer

    cfg = Config.get()
    logger.info(f"启动应用: {cfg['server']['host']}:{cfg['server']['port']}")

    # 初始化数据库
    db = get_db()
    await db.init()

    # 启动 Kafka 消费者（后台线程）
    kafka_consumer = KafkaConsumer()
    kafka_consumer.start(handler=run_pipeline_sync)
    logger.info(f"Kafka 消费者已启动 (fallback={kafka_consumer.is_fallback})")

    logger.info("应用启动完成 ✓")


@app.on_event("shutdown")
async def on_shutdown():
    """关闭时：停止 Kafka 消费者"""
    global kafka_consumer
    if kafka_consumer:
        kafka_consumer.stop()
        logger.info("Kafka 消费者已停止")


# ---------- 前端入口 ----------
@app.get("/")
async def serve_index():
    """返回前端页面"""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    raise HTTPException(status_code=404, detail="前端页面不存在")


@app.get("/favicon.ico")
async def favicon():
    """空 favicon，避免 404"""
    return {"message": "no favicon"}


# ---------- 启动 ----------
if __name__ == "__main__":
    import uvicorn

    cfg_server = Config.get_section('server')
    uvicorn.run(
        "app.main:app",
        host=cfg_server['host'],
        port=cfg_server['port'],
        reload=cfg_server.get('reload', True),
    )
