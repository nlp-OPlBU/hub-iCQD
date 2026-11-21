from functools import lru_cache

from langchain_openai import ChatOpenAI

from .config import get_settings


@lru_cache(maxsize=4)
def get_llm(temperature: float | None = None) -> ChatOpenAI:
    """
    获取并缓存 ChatOpenAI 实例，避免每次请求重复读取环境变量和重建客户端。
    """
    settings = get_settings()
    resolved_temperature = (
        settings.sentiment_temperature if temperature is None else temperature
    )
    return ChatOpenAI(
        model=settings.model_name,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=resolved_temperature,
    )
