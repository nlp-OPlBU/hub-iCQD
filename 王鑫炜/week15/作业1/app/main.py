import logging
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .shared import Framework, ModelError, Reply, ReplyRequest

ROOT = Path(__file__).resolve().parents[1]
LOGGER = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(ROOT / ".env")
    configured = os.getenv("WORKFLOWS", "langgraph,agents").split(",")
    frameworks = [Framework(name.strip()) for name in configured]
    # 为识别和回应分别建进程池，FastAPI 关闭时统一回收工作进程。
    context = multiprocessing.get_context("spawn")
    with (
        ProcessPoolExecutor(max_workers=1, mp_context=context) as classifier_pool,
        ProcessPoolExecutor(max_workers=1, mp_context=context) as responder_pool,
    ):
        flows = {}
        # 按配置加载框架，Docker 中的 Agents 版本无需安装 LangGraph。
        for framework in frameworks:
            if framework == Framework.LANGGRAPH:
                from .langgraph_flow import build_flow
            else:
                from .agents_flow import build_flow

            flows[framework] = build_flow(classifier_pool, responder_pool)
        app.state.flows = flows
        yield


app = FastAPI(title="情绪回应助手", lifespan=lifespan)
app.mount("/web", StaticFiles(directory=ROOT / "web"), name="web")


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(ROOT / "web/index.html")


@app.get("/api/health")
async def health():
    # 容器用此接口检查服务，网页用返回的框架列表生成下拉选项。
    return {"status": "ok", "frameworks": list(app.state.flows)}


@app.post("/api/reply")
async def get_reply(request: ReplyRequest) -> Reply:
    flow = app.state.flows.get(request.framework)
    if flow is None:
        raise HTTPException(400, "当前服务未启用这个框架。")
    if not os.getenv("OPENAI_API_KEY", "").strip():
        raise HTTPException(503, "请在服务端配置 OPENAI_API_KEY 后重启服务。")
    try:
        return await flow(request.text)
    except ModelError as error:
        LOGGER.warning("模型阶段失败（%s）", error)
        raise HTTPException(502, "模型调用失败，请检查模型配置或稍后重试。") from None
    except BrokenProcessPool:
        LOGGER.error("情绪处理进程已退出")
        raise HTTPException(503, "处理进程不可用，请重启服务。") from None
