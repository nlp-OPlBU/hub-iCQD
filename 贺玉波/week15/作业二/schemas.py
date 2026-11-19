"""三个进程共享的数据契约。"""

from enum import Enum

from pydantic import BaseModel, Field


class Sentiment(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    NEUTRAL = "neutral"


class SentimentResult(BaseModel):
    sentiment: Sentiment
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""


class ClassifyRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class RespondRequest(ClassifyRequest):
    sentiment: Sentiment


class RespondResponse(BaseModel):
    reply: str


class AnalysisResponse(BaseModel):
    text: str
    sentiment: Sentiment
    confidence: float
    reasoning: str
    response_text: str
    provider: str
    elapsed_ms: int
