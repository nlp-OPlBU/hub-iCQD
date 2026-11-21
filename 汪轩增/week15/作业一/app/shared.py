import json
import os
import re
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

=load_dotenv()
load_dotenv(BASE_DIR / ".env")

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

Strategy = Literal["encourage", "comfort", "guide"]
BackendName = Literal["agents", "langgraph"]


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
    backend: BackendName | None = None


class ChatResponse(BaseModel):
    backend: str
    model: str
    sentiment: SentimentResult
    strategy: Strategy
    reply: str


STRATEGY_BY_LABEL: dict[str, str] = {
    "positive": "encourage",
    "negative": "comfort",
    "neutral": "guide",
}


# ---------------------------------------------------------------------------
# 进程 2 的提示词（两个框架共用）
# ---------------------------------------------------------------------------
REPLY_PROMPTS: dict[str, str] = {
    "encourage": """你是「鼓励进程」，擅长用中文给人真诚的鼓励。
用户刚刚表达了正向情绪。请先具体回应对方分享的内容，再肯定对方做得好的地方，
帮助对方把这份积极感受延续下去。语气温暖、自然，2~4 句话。
不要使用空洞套话，不要提到情感分析，也不要说自己是 AI。""",
    "comfort": """你是「排解进程」，擅长用中文陪伴和安抚情绪低落的人。
用户正处于负面情绪。请先共情、接纳对方的感受，不评判、不急着说教；
再温和地帮助对方梳理情绪；最后给一个很小、可执行的缓解建议。语气真诚，2~4 句话。
不要提到情感分析，也不要说自己是 AI。""",
    "guide": """你是「引导进程」，是一位温和的中文对话助手。
用户消息的情绪比较中性。请自然回应用户的内容，并温和地邀请对方多说一点自己的想法或感受。
2~3 句话，不要提到情感分析，也不要说自己是 AI。""",
}


# ---------------------------------------------------------------------------
# 情感结果解析
# ---------------------------------------------------------------------------
def normalize_label(raw: Any) -> str:
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


def extract_json_object(text: str) -> dict[str, Any]:
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


def _validate_sentiment(data: dict[str, Any]) -> SentimentResult:
    data = dict(data)
    data["label"] = normalize_label(data.get("label"))

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


def parse_sentiment_text(text: str) -> SentimentResult:
    """解析纯文本中的 JSON 结果（LangGraph 使用）。"""
    return _validate_sentiment(extract_json_object(text))


def parse_sentiment_value(value: Any) -> SentimentResult:
    """解析工具返回值，兼容 Pydantic 实例、dict 和 JSON 字符串（Agents 使用）。"""
    if isinstance(value, SentimentResult):
        return value
    if isinstance(value, dict):
        return _validate_sentiment(value)
    if isinstance(value, str):
        return parse_sentiment_text(value)
    raise ValueError(f"无法解析的情感识别结果：{type(value).__name__}")
