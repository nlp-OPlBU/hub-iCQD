from functools import lru_cache

from agents import AsyncOpenAI, OpenAIChatCompletionsModel, set_tracing_disabled

from .config import get_settings


@lru_cache(maxsize=1)
def get_openai_client() -> AsyncOpenAI:
    """创建并复用异步 OpenAI 兼容客户端，避免每次请求重复创建连接池。"""
    settings = get_settings()
    return AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
    )


@lru_cache(maxsize=1)
def build_model() -> OpenAIChatCompletionsModel:
    """
    构建并缓存适用于 OpenAI Agents SDK 的聊天补全模型实例。
    兼容 DeepSeek 等 OpenAI 兼容接口，并关闭默认 OpenAI Tracing 上报。
    """
    set_tracing_disabled(True)
    settings = get_settings()
    return OpenAIChatCompletionsModel(
        model=settings.model_name,
        openai_client=get_openai_client(),
    )
