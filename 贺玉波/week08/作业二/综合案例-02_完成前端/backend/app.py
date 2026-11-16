# -*- coding: utf-8 -*-
"""深度研究助手后端服务。

启动（在项目根目录，以便加载 .env）：
    uvicorn backend.app:app --reload --port 8000
或：
    bash start.sh

接口：
    POST /api/research        发起一次研究（202，立即返回 research_id）
    GET  /api/research/{rid}  查询研究状态 / 结果（轮询用）
    GET  /api/research        研究列表
    GET  /health              健康检查
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import config, research, storage
from .models import ResearchRecord, ResearchRequest

# 允许前端跨域调用（来源由 FRONTEND_ORIGIN 环境变量控制，见 .env / start.sh）
_default_origin = "http://localhost:3000"
_ALLOWED_ORIGINS = list(
    dict.fromkeys(
        [
            os.environ.get("FRONTEND_ORIGIN", _default_origin),
            _default_origin,
            "http://127.0.0.1:3000",
            "http://localhost:8002",
            "http://127.0.0.1:8002",
        ]
    )
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    config.ensure_data_dir()
    # 打开 backend.*（含各 agent）的 INFO 日志，uvicorn 控制台可见研究过程；
    # uvicorn 自带的 logger 不走 root handler，不会重复输出
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s | %(message)s")
    yield


app = FastAPI(title="深度研究助手 API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/api/research", status_code=202)
async def start_research(req: ResearchRequest) -> dict:
    """发起一次研究：立即返回 research_id，后台异步执行研究循环。"""
    topic = req.topic.strip()
    if not topic:
        raise HTTPException(status_code=400, detail="topic 不能为空")

    research_id = uuid.uuid4().hex
    storage.create(research_id, topic)
    # 后台任务（Runner.run 是 async，直接在事件循环上跑，不占线程池）
    asyncio.create_task(research.run_research(research_id, topic))
    return {"research_id": research_id, "status": "pending"}


@app.get("/api/research/{rid}", response_model=ResearchRecord)
async def get_research(rid: str) -> ResearchRecord:
    """按 research_id 查询研究状态 / 结果。"""
    rec = storage.get(rid)
    if rec is None:
        raise HTTPException(status_code=404, detail="research 不存在")
    return rec


@app.get("/api/research", response_model=list[ResearchRecord])
async def list_research() -> list[ResearchRecord]:
    return storage.list_all()


if __name__ == "__main__":
    # 测试 demo：打印已注册路由后启动开发服务器
    import uvicorn

    print("深度研究助手 API —— 已注册路由：")
    for route in app.routes:
        if getattr(route, "methods", None):
            methods = ",".join(sorted(route.methods - {"HEAD", "OPTIONS"}))
            print(f"  {methods:6s} {route.path}")
    uvicorn.run("backend.app:app", host="127.0.0.1", port=8000, reload=True)
