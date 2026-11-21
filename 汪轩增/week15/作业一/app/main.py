import os
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .shared import (
    API_KEY,
    MODEL,
    ChatRequest,
    ChatResponse,
    MissingConfigError,
)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
VALID_WORKFLOWS = ("agents", "langgraph")


def _parse_workflows() -> tuple[str, ...]:
    """WORKFLOWS 支持 both / all / agents / langgraph / agents,langgraph。"""
    raw = (os.getenv("WORKFLOWS") or "both").strip().lower()
    if raw in ("", "both", "all"):
        return ("agents", "langgraph")

    names = tuple(
        dict.fromkeys(
            part.strip()
            for part in raw.split(",")
            if part.strip() in VALID_WORKFLOWS
        )
    )
    return names or ("agents", "langgraph")


ENABLED_WORKFLOWS = _parse_workflows()
DEFAULT_WORKFLOW = ENABLED_WORKFLOWS[0]

app = FastAPI(title="情感陪伴助手 · 双后端", version="2.0.0")

if WEB_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """首页：静态聊天页面。"""
        return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health() -> dict[str, Any]:
    """前端和 Docker 健康检查使用的接口，不调用模型。"""
    return {
        "status": "ok",
        "model": MODEL,
        "configured": bool(API_KEY),
        "workflows": list(ENABLED_WORKFLOWS),
        "default_workflow": DEFAULT_WORKFLOW,
    }


async def _dispatch(backend: str, message: str) -> ChatResponse:
    """懒加载对应后端，避免 agents-only 镜像因为缺少 LangGraph 依赖而启动失败。"""
    if backend not in ENABLED_WORKFLOWS:
        raise HTTPException(
            status_code=503,
            detail=(
                f"当前服务未启用 {backend} 后端"
                f"（WORKFLOWS={','.join(ENABLED_WORKFLOWS)}）。"
            ),
        )

    try:
        if backend == "agents":
            from .agents_flow import run
        else:
            from .langgraph_flow import run

        return await run(message)
    except MissingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ImportError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"{backend} 后端依赖未安装：{str(exc)[:300]}",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:  # 模型鉴权、网络、解析失败等
        raise HTTPException(
            status_code=502,
            detail=f"后端处理失败：{str(exc)[:500]}",
        ) from exc


def _clean_message(request: ChatRequest) -> str:
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")
    return message


@app.post("/api/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """统一入口，请求体里的 backend 可选，缺省使用 WORKFLOWS 的第一个实现。"""
    return await _dispatch(request.backend or DEFAULT_WORKFLOW, _clean_message(request))


@app.post("/api/agents/chat", response_model=ChatResponse)
async def chat_agents(request: ChatRequest) -> ChatResponse:
    """显式调用 OpenAI Agents 后端。"""
    return await _dispatch("agents", _clean_message(request))


@app.post("/api/langgraph/chat", response_model=ChatResponse)
async def chat_langgraph(request: ChatRequest) -> ChatResponse:
    """显式调用 LangGraph 后端。"""
    return await _dispatch("langgraph", _clean_message(request))


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("PORT", "8000")),
        reload=False,
    )
