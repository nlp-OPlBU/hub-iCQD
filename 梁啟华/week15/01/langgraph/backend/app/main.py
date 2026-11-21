"""FastAPI entry point: accepts frontend input and orchestrates the LangGraph."""

import logging
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .emotion import EmotionParseError
from .llm import LLMNotConfiguredError, create_chat_model
from .schemas import ChatRequest, ChatResponse
from .workflow import EmotionalSupportWorkflow

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="情感识别（流程 1）→ 策略回复（流程 2），由 LangGraph 状态图编排。",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@lru_cache
def get_workflow() -> EmotionalSupportWorkflow:
    """Create the graph lazily so the API can start without a key.

    A missing key is reported as a clear 503 when the chat endpoint is used,
    rather than crashing the whole backend at import time.
    """

    return EmotionalSupportWorkflow(create_chat_model(settings))


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "framework": "langgraph",
        "model": settings.deepseek_model,
        "llm_configured": settings.is_llm_configured,
    }


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest) -> ChatResponse:
    try:
        workflow = get_workflow()
        return await workflow.run(payload.message)
    except LLMNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except EmotionParseError as exc:
        logger.warning("Emotion parsing failed: %s", exc)
        raise HTTPException(
            status_code=502,
            detail="情感识别结果格式异常，请稍后重试。",
        ) from exc
    except Exception as exc:
        logger.exception("Chat request failed")
        raise HTTPException(
            status_code=502,
            detail="模型服务暂时不可用，请稍后重试。",
        ) from exc
