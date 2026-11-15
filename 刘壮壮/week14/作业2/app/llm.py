"""Streaming chat completion adapter for DeepSeek's OpenAI-compatible API."""
import json
import re

import httpx

from app.config import get_settings


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    other = len(re.sub(r"[\u4e00-\u9fff\s]", "", text))
    if not cjk and not other:
        return 0
    return max(1, int(round(cjk * 0.85 + other / 3.8)))


def estimate_messages_tokens(messages: list[dict]) -> int:
    total = 0
    for msg in messages or []:
        total += 4
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "text":
                    total += estimate_text_tokens(str(part.get("text", "")))
                elif part.get("type") == "image_url":
                    total += 256
    return max(1, total) if messages else 0


async def stream_answer(messages, usage_out: dict[str, int] | None = None):
    settings = get_settings()
    generated: list[str] = []
    try:
        if settings.adapter_mode != "real":
            # A visible local stand-in keeps the full API interaction testable offline.
            for part in ("Local adapter response. ", "Configure DeepSeek to enable generated answers."):
                generated.append(part)
                yield part
            return
        if not settings.deepseek_api_key:
            raise RuntimeError("DeepSeek API key is not configured")
        headers = {"Authorization": f"Bearer {settings.deepseek_api_key}", "Content-Type": "application/json"}
        payload = {
            "model": settings.deepseek_model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        async with httpx.AsyncClient(timeout=httpx.Timeout(120, connect=15)) as client:
            async with client.stream("POST", settings.deepseek_api_url, headers=headers, json=payload) as response:
                if response.status_code in (400, 422):
                    fallback_payload = {"model": settings.deepseek_model, "messages": messages, "stream": True}
                    async with client.stream("POST", settings.deepseek_api_url, headers=headers, json=fallback_payload) as retry_resp:
                        retry_resp.raise_for_status()
                        async for delta in _iter_sse_deltas(retry_resp, usage_out):
                            generated.append(delta)
                            yield delta
                    return
                response.raise_for_status()
                async for delta in _iter_sse_deltas(response, usage_out):
                    generated.append(delta)
                    yield delta
    finally:
        if usage_out is not None and (generated or usage_out):
            est_in = estimate_messages_tokens(messages)
            est_out = estimate_text_tokens("".join(generated))
            input_tokens = int(usage_out.get("input_tokens") or est_in)
            output_tokens = int(usage_out.get("output_tokens") or est_out)
            total_tokens = int(usage_out.get("total_tokens") or (input_tokens + output_tokens))
            usage_out["input_tokens"] = input_tokens
            usage_out["output_tokens"] = output_tokens
            usage_out["total_tokens"] = total_tokens


async def _iter_sse_deltas(response: httpx.Response, usage_out: dict[str, int] | None):
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = event.get("usage")
        if isinstance(usage, dict) and usage_out is not None:
            if usage.get("prompt_tokens") is not None:
                usage_out["input_tokens"] = int(usage["prompt_tokens"])
            if usage.get("completion_tokens") is not None:
                usage_out["output_tokens"] = int(usage["completion_tokens"])
            if usage.get("total_tokens") is not None:
                usage_out["total_tokens"] = int(usage["total_tokens"])
        choices = event.get("choices") or []
        if choices:
            delta = choices[0].get("delta", {}).get("content")
            if delta:
                yield delta
