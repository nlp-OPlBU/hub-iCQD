from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from .config import settings


class _LazyStructuredLLM:
    """延迟构造的「结构化输出 LLM」。"""

    def __init__(
        self,
        output_schema: type[Any],
        *,
        model: str | None = None,
        temperature: float = 0.0,
    ) -> None:
        self._output_schema = output_schema
        self._model = model or settings.primary_model
        self._temperature = temperature
        self._runnable = None

    def _ensure(self):
        if self._runnable is None:
            kwargs: dict[str, Any] = {}
            # DeepSeek 默认开启思考模式，而思考模式下不支持强制 tool_choice
            # （结构化输出的 function_calling 方式），需显式关闭思考模式。
            if "deepseek" in settings.base_url:
                kwargs["extra_body"] = {"thinking": {"type": "disabled"}}

            llm = ChatOpenAI(
                model=self._model,
                base_url=settings.base_url,
                temperature=self._temperature,
                timeout=60,
                max_retries=2,
                **kwargs,
            )
            # DeepSeek / DashScope 不支持 response_format=json_schema（默认的
            # method="json_schema" 会报 400），改用 function_calling：把 Pydantic
            # schema 转成工具调用，任何支持 tool calling 的 OpenAI 兼容端点都可用。
            self._runnable = llm.with_structured_output(
                self._output_schema, method="function_calling"
            )
        return self._runnable

    async def ainvoke(self, messages):
        return await self._ensure().ainvoke(messages)


def make_structured_llm(
    output_schema: type[Any],
    *,
    model: str | None = None,
    temperature: float = 0.0,
) -> _LazyStructuredLLM:
    """构造一个带结构化输出的 LLM（对应 openai-agents 的 Agent + output_type）。"""
    return _LazyStructuredLLM(output_schema, model=model, temperature=temperature)


async def invoke_structured(llm, system_prompt: str, prompt: str):
    """以 system + user 两条消息调用结构化 LLM，返回 Pydantic 对象。

    对应 openai-agents 的 ``(await Runner.run(agent, prompt)).final_output``。
    """
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt),
    ]
    return await llm.ainvoke(messages)
