"""后端进程：接受前端输入，串联进程1（8101）与进程2（8102）。"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from config import BASE_DIR, settings
from schemas import AnalysisResponse, ClassifyRequest, RespondRequest, SentimentResult

STATIC_DIR = BASE_DIR / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
    try:
        yield
    finally:
        await app.state.client.aclose()


app = FastAPI(title="后端进程 · 情感分析网关", lifespan=lifespan)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(request: ClassifyRequest) -> AnalysisResponse:
    text = request.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="输入不能为空")

    started = time.perf_counter()
    client: httpx.AsyncClient = app.state.client

    try:
        classify_response = await client.post(
            f"{settings.sentiment_url}/classify", json={"text": text}
        )
        classify_response.raise_for_status()
        result = SentimentResult.model_validate(classify_response.json())

        respond_response = await client.post(
            f"{settings.response_url}/respond",
            json={"text": text, "sentiment": result.sentiment.value},
        )
        respond_response.raise_for_status()
        reply = respond_response.json()["reply"]
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"下游进程不可用，请确认进程1（{settings.sentiment_url}）和进程2（{settings.response_url}）已启动：{exc}",
        ) from exc

    return AnalysisResponse(
        text=text,
        sentiment=result.sentiment,
        confidence=result.confidence,
        reasoning=result.reasoning,
        response_text=reply,
        provider="langgraph",
        elapsed_ms=int((time.perf_counter() - started) * 1000),
    )


@app.get("/health")
async def health() -> dict:
    services: dict[str, str] = {}
    client: httpx.AsyncClient = app.state.client
    for name, url in (("sentiment", settings.sentiment_url), ("response", settings.response_url)):
        try:
            response = await client.get(f"{url}/health", timeout=2.0)
            services[name] = "ok" if response.status_code == 200 else f"http_{response.status_code}"
        except httpx.HTTPError:
            services[name] = "unreachable"
    status = "ok" if all(value == "ok" for value in services.values()) else "degraded"
    return {"status": status, "service": "backend", "framework": "langgraph", "services": services}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=settings.backend_port)
