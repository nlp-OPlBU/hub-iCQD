import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 确保无论从哪个工作目录启动，都可以正确导入 backend 包
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from .config import get_settings  # noqa: E402
from .graph import run_sentiment_workflow  # noqa: E402

app = FastAPI(
    title="LangGraph 情感识别与回应后端服务",
    description="串联进程 1（情感识别）与进程 2（正向鼓励 / 负面排解）",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ChatRequest(BaseModel):
    text: str = Field(..., min_length=1, description="前端传入的用户文本")


class ChatResponse(BaseModel):
    user_input: str
    sentiment: str
    sentiment_reason: str
    reply: str


@app.get("/health")
def health_check():
    return {"status": "ok"}


@app.post("/api/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    user_text = request.text.strip()
    if not user_text:
        raise HTTPException(status_code=400, detail="输入内容不能为空")

    result = run_sentiment_workflow(user_text)
    return ChatResponse(
        user_input=result["user_input"],
        sentiment=result["sentiment"],
        sentiment_reason=result["sentiment_reason"],
        reply=result["reply"],
    )


if __name__ == "__main__":
    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=False,
    )
