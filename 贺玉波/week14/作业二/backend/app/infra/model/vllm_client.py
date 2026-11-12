"""vLLM（OpenAI 兼容）客户端。

P1 只用探活；P5 增加一次性补全与流式补全两种调用方式。
流式只在 API 层使用，保证 token 边生成边下发。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger, log_event

logger = get_logger("app.infra.vllm")

# 生成是幂等调用，5xx / 429 / 网络抖动值得重试；4xx 是请求本身的问题，重试只会浪费 GPU
RETRY_ATTEMPTS = 2
RETRY_BACKOFF_S = 0.4


def _headers() -> dict[str, str]:
    api_key = get_settings().vllm.api_key
    return {"Authorization": f"Bearer {api_key}"} if api_key else {}


def _payload(messages: list[dict[str, Any]], *, stream: bool, max_tokens: int | None, temperature: float | None) -> dict[str, Any]:
    settings = get_settings().vllm
    payload = {
        "model": settings.model,
        "messages": messages,
        "max_tokens": max_tokens or settings.max_tokens,
        "temperature": settings.temperature if temperature is None else temperature,
        "stream": stream,
    }
    # 本地 vLLM 不认识 thinking 字段，只有云端（DeepSeek）才带
    if settings.is_cloud:
        payload["thinking"] = {"type": settings.thinking}
    return payload


async def list_models(timeout: float | None = None) -> list[str]:
    settings = get_settings().vllm
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=timeout or settings.timeout) as client:
        response = await client.get("/models", headers=_headers())
        response.raise_for_status()
        return [item["id"] for item in response.json().get("data", [])]


async def ping_vllm(timeout: float = 1.0) -> str:
    settings = get_settings().vllm
    models = await list_models(timeout=timeout)
    if not models:
        return "no-model"
    return models[0] if settings.model in models else f"{models[0]}（期望 {settings.model}）"


async def chat_completion(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> str:
    """一次性返回完整回答；异常原样抛出，由 service 映射错误码。

    只对可能自愈的错误重试：网络抖动、5xx、429。流式接口不在重试范围内
    （已经吐出的 token 无法撤回，重试会造成重复内容）。
    """
    last: Exception | None = None
    for attempt in range(RETRY_ATTEMPTS + 1):
        try:
            return await _complete_once(
                messages, max_tokens=max_tokens, temperature=temperature, timeout=timeout
            )
        except Exception as exc:
            last = exc
            if attempt >= RETRY_ATTEMPTS or not _retryable(exc):
                raise
            log_event(
                logger,
                "vllm_retry",
                level=30,
                attempt=attempt + 1,
                error=f"{type(exc).__name__}",
            )
            await asyncio.sleep(RETRY_BACKOFF_S * (2**attempt))
    raise last  # pragma: no cover - 循环内必然 return 或 raise


async def _complete_once(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None,
    temperature: float | None,
    timeout: float | None,
) -> str:
    settings = get_settings().vllm
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=timeout or settings.timeout) as client:
        response = await client.post(
            "/chat/completions",
            json=_payload(messages, stream=False, max_tokens=max_tokens, temperature=temperature),
            headers=_headers(),
        )
        response.raise_for_status()
        choices = response.json().get("choices") or [{}]
        return (choices[0].get("message") or {}).get("content") or ""


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500 or exc.response.status_code == 429
    return False


async def stream_chat(
    messages: list[dict[str, Any]],
    *,
    max_tokens: int | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
) -> AsyncIterator[str]:
    """按 SSE 协议逐段产出 token；遇到 ``[DONE]`` 结束。"""
    settings = get_settings().vllm
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=timeout or settings.timeout) as client:
        async with client.stream(
            "POST",
            "/chat/completions",
            json=_payload(messages, stream=True, max_tokens=max_tokens, temperature=temperature),
            headers=_headers(),
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:  # 个别版本会插入空行/心跳
                    continue
                choices = chunk.get("choices") or []
                delta = (choices[0].get("delta") or {}).get("content") if choices else None
                if delta:
                    yield delta
