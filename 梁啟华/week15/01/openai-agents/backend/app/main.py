"""FastAPI entry point: accepts frontend input and orchestrates the Agents."""

import logging
from contextlib import asynccontextmanager
from functools import lru_cache

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .emotion import EmotionParseError
from .schemas import ChatRequest, ChatResponse
from .workflow import AgentsEmotionWorkflow, LLMNotConfiguredError

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


@lru_cache
def get_workflow() -> AgentsEmotionWorkflow:
    """Create the Agents lazily so the API can start without a key."""

    return AgentsEmotionWorkflow(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if get_workflow.cache_info().currsize:
        await get_workflow().aclose()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="情感识别 Agent（流程 1）→ 策略回复 Agent（流程 2）。",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "status": "ok",
        "framework": "openai-agents",
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
