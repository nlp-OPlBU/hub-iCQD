"""Pydantic schemas shared by the API and the LangGraph workflow."""

from typing import Literal

from pydantic import BaseModel, Field, field_validator

EmotionLabel = Literal["positive", "negative", "neutral"]
Strategy = Literal["encouragement", "relief", "neutral"]


class EmotionResult(BaseModel):
    label: EmotionLabel
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = Field(default="", max_length=200)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("消息不能为空")
        return value


class ChatResponse(BaseModel):
    reply: str
    emotion: EmotionResult
    strategy: Strategy
    strategy_label: str
