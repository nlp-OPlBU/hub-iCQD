

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal, TypedDict

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

load_dotenv()
load_dotenv(Path(__file__).with_name(".env"))

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

BACKEND_NAME = "langgraph"
PORT = int(os.getenv("LANGGRAPH_PORT", "8001"))


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


class ChatState(TypedDict, total=False):
    """LangGraph 在进程之间流转的状态。"""

    message: str
    sentiment: dict[str, Any]
    strategy: Literal["encourage", "comfort", "guide"]
    reply: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


class ChatResponse(BaseModel):
    backend: str
    model: str
    sentiment: SentimentResult
    strategy: Literal["encourage", "comfort", "guide"]
    reply: str


# ---------------------------------------------------------------------------
# 模型与解析工具
# ---------------------------------------------------------------------------
@lru_cache(maxsize=4)
def get_llm(temperature: float) -> ChatOpenAI:
    """按需创建 ChatOpenAI 客户端，导入模块时不会因为没有 Key 而失败。"""
    if not API_KEY:
        raise MissingConfigError(
            "未配置 OPENAI_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中填写后重启后端。"
        )
    return ChatOpenAI(
        model=MODEL,
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=temperature,
        timeout=90,
        max_retries=2,
    )


def _content_to_text(content: Any) -> str:
    """把 LangChain 返回的 content（字符串或内容块列表）统一成文本。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(str(block.get("text", "")))
        return "".join(parts)
    return str(content)


def _chat(system_prompt: str, user_prompt: str, temperature: float) -> str:
    llm = get_llm(temperature)
    response = llm.invoke(
        [SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)]
    )
    return _content_to_text(response.content).strip()


def _normalize_label(raw: Any) -> str:
    """把模型可能给出的中英文标签统一成 positive / negative / neutral。"""
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
    """从模型输出中提取 JSON 对象，兼容 ```json 代码块和前后解释文字。"""
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


def _parse_sentiment(text: str) -> SentimentResult:
    data = _extract_json_object(text)
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
# 进程 1：情感识别
# ---------------------------------------------------------------------------
SENTIMENT_SYSTEM_PROMPT = """你是「情感识别进程」，只负责判断用户消息的情感倾向。
判断标准：
- positive：开心、感激、期待、满意、兴奋等正向情绪；
- negative：难过、焦虑、愤怒、失望、孤独、压力等负面情绪；
- neutral：客观陈述、信息询问，或没有明显情绪色彩。
只输出一个 JSON 对象，不要输出 Markdown 代码块、前后解释或任何其他文字。格式：
{"label": "positive|negative|neutral", "emotion": "具体情绪", "confidence": 0.85, "reason": "判断依据"}"""


def analyze_sentiment(message: str) -> SentimentResult:
    """调用模型做情感识别；解析失败时重试一次。"""
    user_prompt = f"请识别下面这条消息的情感：\n{message}"
    last_error: Exception | None = None
    for _ in range(2):
        try:
            raw = _chat(SENTIMENT_SYSTEM_PROMPT, user_prompt, temperature=0.0)
            return _parse_sentiment(raw)
        except (ValueError, ValidationError) as exc:
            # 只有“格式不对”才重试；鉴权、网络等错误直接向上抛出
            last_error = exc
    raise RuntimeError(f"情感识别进程没有返回可解析的结果：{last_error}")


def sentiment_node(state: ChatState) -> dict[str, Any]:
    """进程 1 节点：接受请求，输出情感识别结果。"""
    sentiment = analyze_sentiment(state["message"])
    return {"sentiment": sentiment.model_dump()}


# ---------------------------------------------------------------------------
# 进程 2：按情感分支生成回复
# ---------------------------------------------------------------------------
def _reply_user_prompt(state: ChatState) -> str:
    sentiment = state["sentiment"]
    return (
        f"用户原话：\n{state['message']}\n\n"
        f"（参考信息：识别情绪为「{sentiment['emotion']}」，{sentiment['reason']}。"
        "不要在回复中提及这段参考信息。）"
    )


ENCOURAGE_SYSTEM_PROMPT = """你是「鼓励进程」，擅长用中文给人真诚的鼓励。
用户刚刚表达了正向情绪。请先具体回应对方分享的内容，再肯定对方做得好的地方，
帮助对方把这份积极感受延续下去。语气温暖、自然，2~4 句话。
不要使用空洞套话，不要提到情感分析，也不要说自己是 AI。"""

COMFORT_SYSTEM_PROMPT = """你是「排解进程」，擅长用中文陪伴和安抚情绪低落的人。
用户正处于负面情绪。请先共情、接纳对方的感受，不评判、不急着说教；
再温和地帮助对方梳理情绪；最后给一个很小、可执行的缓解建议。语气真诚，2~4 句话。
不要提到情感分析，也不要说自己是 AI。"""

GUIDE_SYSTEM_PROMPT = """你是「引导进程」，是一位温和的中文对话助手。
用户消息的情绪比较中性。请自然回应用户的内容，并温和地邀请对方多说一点自己的想法或感受。
2~3 句话，不要提到情感分析，也不要说自己是 AI。"""


def make_reply_node(system_prompt: str, strategy: str):
    """进程 2 的工厂：生成鼓励 / 排解 / 引导三个分支节点。"""

    def reply_node(state: ChatState) -> dict[str, Any]:
        reply = _chat(
            system_prompt,
            _reply_user_prompt(state),
            temperature=0.8,
        )
        if not reply:
            raise RuntimeError("回复生成进程返回了空内容")
        return {"strategy": strategy, "reply": reply}

    return reply_node


def route_by_sentiment(state: ChatState) -> str:
    """情感识别结果 -> 进程 2 的具体分支。"""
    return state["sentiment"]["label"]


# ---------------------------------------------------------------------------
# 编排：进程 1 -> 条件路由 -> 进程 2 -> 结束
# ---------------------------------------------------------------------------
def build_graph():
    builder = StateGraph(ChatState)

    builder.add_node("sentiment", sentiment_node)  # 进程 1
    builder.add_node("encourage", make_reply_node(ENCOURAGE_SYSTEM_PROMPT, "encourage"))  # 进程 2-正向
    builder.add_node("comfort", make_reply_node(COMFORT_SYSTEM_PROMPT, "comfort"))  # 进程 2-负面
    builder.add_node("guide", make_reply_node(GUIDE_SYSTEM_PROMPT, "guide"))  # 进程 2-中性

    builder.add_edge(START, "sentiment")
    builder.add_conditional_edges(
        "sentiment",
        route_by_sentiment,
        {
            "positive": "encourage",
            "negative": "comfort",
            "neutral": "guide",
        },
    )
    builder.add_edge("encourage", END)
    builder.add_edge("comfort", END)
    builder.add_edge("guide", END)
    return builder.compile()


graph = build_graph()


# ---------------------------------------------------------------------------
# 后端进程：FastAPI
# ---------------------------------------------------------------------------
app = FastAPI(title="情感陪伴后端 · LangGraph", version="1.0.0")


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
def chat(request: ChatRequest) -> ChatResponse:
    """串联进程 1 和进程 2，返回情感识别结果与回复。"""
    message = request.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="消息不能为空")

    try:
        state = graph.invoke(
            {"message": message},
            config={"recursion_limit": 10},
        )
    except MissingConfigError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:  # 模型鉴权、网络、解析失败等
        raise HTTPException(
            status_code=502,
            detail=f"后端处理失败：{str(exc)[:500]}",
        ) from exc

    return ChatResponse(
        backend=BACKEND_NAME,
        model=MODEL,
        sentiment=SentimentResult.model_validate(state["sentiment"]),
        strategy=state["strategy"],
        reply=state["reply"],
    )


if __name__ == "__main__":
    print(f"LangGraph 后端启动：http://127.0.0.1:{PORT}（模型：{MODEL}）")
    uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="info")
