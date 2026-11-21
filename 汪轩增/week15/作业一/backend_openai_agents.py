

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal

import uvicorn
from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    StopAtTools,
    function_tool,
    set_tracing_disabled,
)
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

# 依次加载：当前工作目录的 .env、本文件同目录的 .env（已存在的环境变量优先）
load_dotenv()
load_dotenv(Path(__file__).with_name(".env"))

set_tracing_disabled(True)

# ---------------------------------------------------------------------------
# 模型配置（兼容多套常见环境变量命名）
# ---------------------------------------------------------------------------
API_KEY = os.getenv("OPENAI_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or ""
BASE_URL = (
    os.getenv("OPENAI_BASE_URL")
    or os.getenv("DEEPSEEK_BASE_URL")
    or "https://api.deepseek.com"
)
MODEL = (
    os.getenv("OPENAI_MODEL")
    or os.getenv("DEEPSEEK_MODEL")
    or os.getenv("MODEL_NAME")
    or "deepseek-flash"
)

BACKEND_NAME = "openai-agents"
PORT = int(os.getenv("AGENTS_PORT", "8002"))


class MissingConfigError(RuntimeError):
    """没有配置模型 API Key 时抛出。"""


# ---------------------------------------------------------------------------
# 数据契约
# ---------------------------------------------------------------------------
class SentimentResult(BaseModel):
    """进程 1 的输出：结构化情感识别结果。"""

    label: Literal["positive", "negative", "neutral"] = Field(description="情感倾向")
    emotion: str = Field(description="具体情绪，如开心、焦虑、愤怒、平静")
    confidence: float = Field(ge=0.0, le=1.0, description="置信度，0~1")
    reason: str = Field(description="不超过 40 字的判断依据")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    backend: str
    model: str
    sentiment: SentimentResult
    strategy: Literal["encourage", "comfort", "guide"]
    reply: str


# ---------------------------------------------------------------------------
# 进程 1：情感识别工具与 Agent
# ---------------------------------------------------------------------------
@function_tool
def report_sentiment(
    label: Annotated[
        Literal["positive", "negative", "neutral"],
        Field(description="情感倾向：positive 正向 / negative 负面 / neutral 中性"),
    ],
    emotion: Annotated[str, Field(description="具体情绪，如开心、焦虑、愤怒、平静")],
    confidence: Annotated[float, Field(description="0~1 之间的置信度")],
    reason: Annotated[str, Field(description="不超过 40 字的判断依据")],
) -> str:
    """提交对用户消息的情感识别结果。每次识别必须且只能调用一次本工具，不要直接回答用户。"""
    result = SentimentResult(
        label=label,
        emotion=emotion.strip() or "平静",
        confidence=max(0.0, min(1.0, float(confidence))),
        reason=reason.strip() or "模型未给出判断依据",
    )
    return result.model_dump_json()


SENTIMENT_INSTRUCTIONS = """你是「情感识别进程」，只负责判断用户消息的情感倾向。
判断标准：
- positive：开心、感激、期待、满意、兴奋等正向情绪；
- negative：难过、焦虑、愤怒、失望、孤独、压力等负面情绪；
- neutral：客观陈述、信息询问，或没有明显情绪色彩。
必须调用 report_sentiment 工具提交结果，不要直接回答用户，也不要输出多余的解释。"""

ENCOURAGE_INSTRUCTIONS = """你是「鼓励进程」，擅长用中文给人真诚的鼓励。
用户刚刚表达了正向情绪。请先具体回应对方分享的内容，再肯定对方做得好的地方，
帮助对方把这份积极感受延续下去。语气温暖、自然，2~4 句话。
不要使用空洞套话，不要提到情感分析，也不要说自己是 AI。"""

COMFORT_INSTRUCTIONS = """你是「排解进程」，擅长用中文陪伴和安抚情绪低落的人。
用户正处于负面情绪。请先共情、接纳对方的感受，不评判、不急着说教；
再温和地帮助对方梳理情绪；最后给一个很小、可执行的缓解建议。语气真诚，2~4 句话。
不要提到情感分析，也不要说自己是 AI。"""

GUIDE_INSTRUCTIONS = """你是「引导进程」，是一位温和的中文对话助手。
用户消息的情绪比较中性。请自然回应用户的内容，并温和地邀请对方多说一点自己的想法或感受。
2~3 句话，不要提到情感分析，也不要说自己是 AI。"""

STRATEGY_BY_LABEL = {
    "positive": "encourage",
    "negative": "comfort",
    "neutral": "guide",
}


def _build_model() -> OpenAIChatCompletionsModel:
    if not API_KEY:
        raise MissingConfigError(
            "未配置 OPENAI_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中填写后重启后端。"
        )
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=90.0,
        max_retries=2,
    )
    return OpenAIChatCompletionsModel(model=MODEL, openai_client=client)


@lru_cache(maxsize=1)
def get_agents() -> dict[str, Agent]:
    """创建四个 Agent：1 个情感识别 Agent + 3 个回复 Agent。"""
    model = _build_model()
    common_settings = ModelSettings(temperature=0.8)

    sentiment_agent = Agent(
        name="情感识别进程",
        instructions=SENTIMENT_INSTRUCTIONS,
        tools=[report_sentiment],
        tool_use_behavior=StopAtTools(stop_at_tool_names=["report_sentiment"]),
        model=model,
        model_settings=ModelSettings(temperature=0.0),
    )
    encourage_agent = Agent(
        name="鼓励进程",
        instructions=ENCOURAGE_INSTRUCTIONS,
        model=model,
        model_settings=common_settings,
    )
    comfort_agent = Agent(
        name="排解进程",
        instructions=COMFORT_INSTRUCTIONS,
        model=model,
        model_settings=common_settings,
    )
    guide_agent = Agent(
        name="引导进程",
        instructions=GUIDE_INSTRUCTIONS,
        model=model,
        model_settings=common_settings,
    )
    return {
        "sentiment": sentiment_agent,
        "encourage": encourage_agent,
        "comfort": comfort_agent,
        "guide": guide_agent,
    }


# ---------------------------------------------------------------------------
# 结果解析
# ---------------------------------------------------------------------------
def _normalize_label(raw: Any) -> str:
    text = str(raw or "").strip().lower()
    mapping = {
        "positive": "positive",
        "正向": "positive",
        "积极": "positive",
        "negative": "negative",
        "负面": "negative",
        "消极": "negative",
        "neutral": "neutral",
        "中性": "neutral",
        "中立": "neutral",
    }
    return mapping.get(text, text)


def _extract_json_object(text: str) -> dict[str, Any]:
    """从文本中提取 JSON 对象，兼容 ```json 代码块和前后解释文字。"""
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.S | re.I)
    if fence:
        cleaned = fence.group(1).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("模型输出中没有找到 JSON 对象")
    data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("模型输出不是 JSON 对象")
    return data


def _parse_sentiment(value: Any) -> SentimentResult:
    """兼容三种形态：工具返回的 JSON 字符串、dict、以及纯文本中的 JSON。"""
    if isinstance(value, SentimentResult):
        return value
    if isinstance(value, dict):
        data = dict(value)
    elif isinstance(value, str):
        data = _extract_json_object(value)
    else:
        raise ValueError(f"无法解析的情感识别结果：{type(value).__name__}")

    data["label"] = _normalize_label(data.get("label"))
    try:
        confidence = float(data.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    if confidence > 1:  # 模型偶尔返回百分数
        confidence = confidence / 100
    data["confidence"] = max(0.0, min(1.0, confidence))
    data["emotion"] = str(data.get("emotion") or "平静").strip()
    data["reason"] = str(data.get("reason") or "模型未给出判断依据").strip()
    return SentimentResult.model_validate(data)


# ---------------------------------------------------------------------------
# 串联进程 1 和进程 2
# ---------------------------------------------------------------------------
async def extract_sentiment(agent: Agent, message: str) -> SentimentResult:
    """进程 1：运行情感识别 Agent，拿到结构化结果。"""
    last_error: Exception | None = None
    for attempt in range(2):
        prompt = message
        if attempt == 1:
            prompt = (
                "上一次没有得到有效结果。请立刻调用 report_sentiment 工具提交情感识别结果，"
                f"不要输出其他内容。\n用户消息：{message}"
            )
        result = await Runner.run(agent, input=prompt, max_turns=3)
        try:
            return _parse_sentiment(result.final_output)
        except (ValueError, ValidationError) as exc:
            last_error = exc
    raise RuntimeError(f"情感识别进程没有返回可解析的结果：{last_error}")


async def run_pipeline(message: str) -> ChatResponse:
    """后端进程的核心逻辑：进程 1 -> 选择进程 2 -> 返回结果。"""
    agents = get_agents()

    sentiment = await extract_sentiment(agents["sentiment"], message)
    strategy = STRATEGY_BY_LABEL[sentiment.label]

    reply_result = await Runner.run(agents[strategy], input=message, max_turns=3)
    reply = str(reply_result.final_output or "").strip()
    if not reply:
        raise RuntimeError("回复生成进程返回了空内容")

    return ChatResponse(
        backend=BACKEND_NAME,
        model=MODEL,
        sentiment=sentiment,
        strategy=strategy,
        reply=reply,
    )


# ---------------------------------------------------------------------------
# 后端进程：FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="情感陪伴后端 · OpenAI Agents", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    """前端用这个接口检查后端是否已启动、Key 是否已配置。"""
    return {
        "backend": BACKEND_NAME,
        "model": MODEL,
        "configured": bool(API_KEY),
        "status": "ok",
    }


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """串联进程 1 和进程 2，返回情感识别结果与回复。"""
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    try:
        return await run_pipeline(message)
    except MissingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # 模型鉴权、网络、解析失败等
        raise HTTPException(
            status_code=502,
            detail=f"后端处理失败：{str(exc)[:500]}",
        ) from exc


if __name__ == "__main__":
    print(f"OpenAI Agents 后端启动：http://127.0.0.1:{PORT}（模型：{MODEL}）")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
