from __future__ import annotations

import json
import time

import openai

from .agents import AgentError
from .mock_data import (
    DEFAULT_PLAN, DEFAULT_READ, DEFAULT_REFINE_DONE,
    DEFAULT_REFINE_NEED_MORE, DEFAULT_REPORT,
)


class LLMError(Exception):
    """LLM 调用失败（重试耗尽、API 错误等）。"""


class LLMParseError(LLMError, AgentError):
    """LLM 输出解析失败（坏 JSON/空响应）重试耗尽。

    多重继承：pipeline 各阶段的 `except AgentError` 子句位于
    `except LLMError` 之前，按首个匹配规则即实现阶段降级；
    而 llm 层测试 `pytest.raises(LLMError)` 因继承关系继续成立。"""


_RETRYABLE = (
    openai.RateLimitError, openai.APITimeoutError,
    openai.APIConnectionError, openai.InternalServerError,
)


class DeepSeekClient:
    def __init__(self, api_key: str, model: str = "deepseek-v4-pro",
                 reasoning_effort: str | None = "max",
                 base_url: str = "https://api.deepseek.com", client=None):
        self.client = client or openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.reasoning_effort = reasoning_effort

    def chat_json(self, system_prompt: str, user_prompt: str,
                  max_retries: int = 3) -> dict:
        kwargs: dict = {
            "model": self.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.reasoning_effort:
            kwargs["reasoning_effort"] = self.reasoning_effort

        last_err: Exception | None = None
        parse_failure = False
        for attempt in range(max_retries):
            try:
                resp = self.client.chat.completions.create(**kwargs)
                text = resp.choices[0].message.content
                if not text:
                    raise ValueError("空响应")
                return json.loads(text)
            except (json.JSONDecodeError, ValueError, TypeError) as e:
                # 输出不是合法 JSON：重试
                last_err = e
                parse_failure = True
            except (openai.BadRequestError, TypeError) as e:
                # reasoning_effort 不被支持：去掉该参数重试一次
                if "reasoning_effort" in str(e) and "reasoning_effort" in kwargs:
                    kwargs.pop("reasoning_effort", None)
                    continue
                raise LLMError(f"API 请求被拒绝: {e}") from e
            except _RETRYABLE as e:
                last_err = e
            except openai.APIError as e:
                raise LLMError(f"API 错误: {e}") from e
            time.sleep(2.0 * (2 ** attempt))
        if parse_failure:
            raise LLMParseError(f"输出解析失败，重试 {max_retries} 次: {last_err}")
        raise LLMError(f"重试 {max_retries} 次后仍失败: {last_err}")


class MockLLM:
    """离线假 LLM：显式响应队列优先；否则按提示词关键词分发内置夹具。"""

    def __init__(self, responses: list[dict] | None = None):
        self.responses = list(responses) if responses is not None else None
        self._refine_calls = 0

    def chat_json(self, system_prompt: str, user_prompt: str,
                  max_retries: int = 3) -> dict:
        if self.responses is not None:
            if not self.responses:
                raise LLMError("MockLLM 响应队列已耗尽")
            return self.responses.pop(0)
        text = system_prompt + user_prompt
        if "规划" in text:
            return DEFAULT_PLAN
        if "补检" in text:
            # 顺序敏感：REFINER 提示词含「已抽取要点」，必须先判「补检」再判「抽取」
            self._refine_calls += 1
            return DEFAULT_REFINE_NEED_MORE if self._refine_calls == 1 else DEFAULT_REFINE_DONE
        if "抽取" in text:
            return DEFAULT_READ
        if "综合" in text:
            return DEFAULT_REPORT
        raise LLMError(f"MockLLM 无法匹配提示词类型: {system_prompt[:50]}")
