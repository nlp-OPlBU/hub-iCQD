"""DeepSeek chat model factory (OpenAI-compatible endpoint)."""

from langchain_openai import ChatOpenAI

from .config import Settings, get_settings


class LLMNotConfiguredError(RuntimeError):
    """Raised when the DeepSeek API key is missing."""


def create_chat_model(settings: Settings | None = None) -> ChatOpenAI:
    settings = settings or get_settings()
    api_key = settings.deepseek_api_key.get_secret_value().strip()
    if not api_key:
        raise LLMNotConfiguredError(
            "尚未配置 DEEPSEEK_API_KEY。请复制 backend/.env.example 为 backend/.env 并填写密钥。"
        )

    return ChatOpenAI(
        model=settings.deepseek_model,
        api_key=api_key,
        base_url=settings.deepseek_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout_seconds,
        max_retries=2,
    )
