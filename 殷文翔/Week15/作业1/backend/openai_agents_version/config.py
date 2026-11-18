from pydantic_settings import BaseSettings
from pydantic import BaseModel, Field
from typing import Literal
import os


class Settings(BaseSettings):
    openai_api_key: str = os.getenv("DEEPSEEK_API_KEY")
    openai_base_url: str = os.getenv("DEEPSEEK_URL")
    default_model: str = "deepseek-flash"
    sentiment_analyzer_request_url: str = "http://localhost:8001/sentiment"
    response_request_url: str = "http://localhost:8002/response"


setting = Settings()


def get_settings() -> Settings:
    return setting


class AnalyzeResult(BaseModel):
    result: Literal["positive", "negative"] = Field(description="情感分析结果，只能是 'positive' 或 'negative'")


class Response(BaseModel):
    result: Literal["positive", "negative"]
