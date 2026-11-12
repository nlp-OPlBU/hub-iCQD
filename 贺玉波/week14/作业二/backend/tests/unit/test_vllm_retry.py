"""vLLM 非流式调用的重试策略：只重试可能自愈的错误。"""

from __future__ import annotations

import httpx
import pytest

from app.infra.model import vllm_client

MESSAGES = [{"role": "user", "content": "你好"}]


def _status_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://vllm.local/chat/completions")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError(f"HTTP {code}", request=request, response=response)


def test_retryable_classification() -> None:
    assert vllm_client._retryable(httpx.ConnectError("boom")) is True
    assert vllm_client._retryable(_status_error(500)) is True
    assert vllm_client._retryable(_status_error(429)) is True
    assert vllm_client._retryable(_status_error(400)) is False
    assert vllm_client._retryable(ValueError("bad")) is False


async def test_retries_once_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def flaky(messages, **_: object) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("connection reset")
        return "答案"

    monkeypatch.setattr(vllm_client, "_complete_once", flaky)
    monkeypatch.setattr(vllm_client, "RETRY_BACKOFF_S", 0)

    assert await vllm_client.chat_completion(MESSAGES) == "答案"
    assert calls["n"] == 2


async def test_non_retryable_raises_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def bad_request(messages, **_: object) -> str:
        calls["n"] += 1
        raise _status_error(400)

    monkeypatch.setattr(vllm_client, "_complete_once", bad_request)

    with pytest.raises(httpx.HTTPStatusError):
        await vllm_client.chat_completion(MESSAGES)
    assert calls["n"] == 1


async def test_retries_exhausted_bubbles_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    async def always_down(messages, **_: object) -> str:
        calls["n"] += 1
        raise _status_error(503)

    monkeypatch.setattr(vllm_client, "_complete_once", always_down)
    monkeypatch.setattr(vllm_client, "RETRY_BACKOFF_S", 0)
    monkeypatch.setattr(vllm_client, "RETRY_ATTEMPTS", 2)

    with pytest.raises(httpx.HTTPStatusError):
        await vllm_client.chat_completion(MESSAGES)
    assert calls["n"] == 3
