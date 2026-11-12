"""LangChain 模型装配：提示词模板 + 结构化输出链。

设计要点：

- 每个 Agent 仍然只做单一决策，模块级定义、复用同一个链对象。
- 底层使用 ``ChatOpenAI``（DashScope / DeepSeek 等 OpenAI 兼容端点）。
- 结构化输出用「工具调用 + Pydantic 校验」实现，不再依赖 openai-agents 的
  ``output_type`` / ``Runner.run``。
- ``tool_choice`` 默认是 ``auto``：思考模式（thinking mode）的模型会直接拒绝
  强制指定工具（``tool_choice`` 指定到具体函数），只接受 ``auto``；同时部分
  兼容端点也不支持 OpenAI 的 ``json_schema`` 响应格式，因此统一用工具定义
  约束字段，并在模型没有调用工具时回退解析正文里的 JSON。
- 链在首次调用时才构建，因此导入模块、运行单元测试都不需要 API key。
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, TypeVar

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, ValidationError

from .config import settings

logger = logging.getLogger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)

# 模型返回无法解析时的重试提示，追加在用户输入之后。
_PARSE_RETRY_HINT = (
    "\n\n上一次输出无法解析为约定的结构化结果：{error}\n"
    "请严格按给定的字段和类型重新输出，不要添加解释性文字。"
)

# 追加到 system 提示之后，让模型在没有强制 tool_choice 的情况下也稳定调用工具。
_TOOL_RULE = (
    "\n\n输出约定：必须调用工具 {tool} 一次性给出全部字段，"
    "除工具调用外不要输出任何自然语言或 Markdown。"
)

# 允许直接透传给 OpenAI 兼容端点的 tool_choice 取值。
_PASSTHROUGH_TOOL_CHOICES = frozenset({"auto", "required", "none"})


class StructuredAgent:
    """一个只负责单一决策的 LLM Agent。

    结构与 ``ref/agent-demo.py`` 保持一致：模块级定义、显式提示词、
    Pydantic 结构化输出；区别是底层换成 LangChain 的
    ``prompt | ChatOpenAI.bind_tools(...)`` 可运行链。
    """

    def __init__(
        self,
        *,
        name: str,
        instructions: str,
        output_type: type[TModel],
        model: str,
        temperature: float = 0.0,
    ) -> None:
        self.name = name
        self.output_type = output_type
        self.model_name = model
        self.temperature = temperature
        self.tool_name = output_type.__name__
        self.prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    instructions.strip()
                    + _TOOL_RULE.format(tool=self.tool_name),
                ),
                ("human", "{input}"),
            ]
        )
        self._chain: Any | None = None

    # ------------------------------------------------------------------ 构建
    def create_model(self) -> ChatOpenAI:
        """按需创建 ChatOpenAI 实例，避免导入期强依赖 API key。"""

        return ChatOpenAI(
            model=self.model_name,
            temperature=self.temperature,
            base_url=settings.base_url or None,
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    def tool_schema(self) -> dict[str, Any]:
        """把 Pydantic 输出模型声明成 OpenAI 兼容的 function 定义。"""

        return {
            "type": "function",
            "function": {
                "name": self.tool_name,
                "description": f"返回 {self.output_type.__name__} 结构化结果",
                "parameters": self.output_type.model_json_schema(),
            },
        }

    @property
    def chain(self) -> Any:
        """``prompt -> model(bind_tools) -> 解析`` 的链，懒加载复用。"""

        if self._chain is None:
            self._chain = _StructuredToolChain(
                prompt=self.prompt,
                model=self.create_model(),
                output_type=self.output_type,
                tool=self.tool_schema(),
                tool_choice=_resolve_tool_choice(
                    settings.tool_choice, self.tool_name
                ),
            )
        return self._chain

    # ------------------------------------------------------------------ 调用
    async def ainvoke(self, text: str) -> TModel:
        """调用模型并返回结构化结果；解析失败时带错误反馈重试一次。"""

        started = time.perf_counter()
        parsed, raw, error = self._unpack(await self.chain.ainvoke({"input": text}))

        if parsed is None:
            logger.warning(
                "stage=llm_parse_failed agent=%s error=%s",
                self.name,
                _preview(error),
            )
            retry_text = text + _PARSE_RETRY_HINT.format(error=error)
            parsed, raw, error = self._unpack(
                await self.chain.ainvoke({"input": retry_text})
            )

        if parsed is None or not isinstance(parsed, self.output_type):
            raise TypeError(
                f"{self.name} 未能返回 {self.output_type.__name__}：{_preview(error)}"
            )

        usage = getattr(raw, "usage_metadata", None) or {}
        logger.info(
            "stage=llm agent=%s model=%s elapsed=%.2fs input_tokens=%s output_tokens=%s",
            self.name,
            self.model_name,
            time.perf_counter() - started,
            usage.get("input_tokens"),
            usage.get("output_tokens"),
        )
        return parsed

    @staticmethod
    def _unpack(result: Any) -> tuple[Any, Any, Any]:
        """兼容 ``include_raw=True`` 的返回结构与直出的 Pydantic 结果。"""

        if isinstance(result, dict) and "parsed" in result:
            return result.get("parsed"), result.get("raw"), result.get("parsing_error")
        return result, None, None


class _StructuredToolChain:
    """``prompt | model.bind_tools(...)`` 的最小封装。

    对外只暴露 ``ainvoke({"input": ...})``，返回
    ``{"parsed", "raw", "parsing_error"}``，与 ``include_raw=True`` 的形状一致。
    """

    def __init__(
        self,
        *,
        prompt: ChatPromptTemplate,
        model: ChatOpenAI,
        output_type: type[BaseModel],
        tool: dict[str, Any],
        tool_choice: Any,
    ) -> None:
        self.output_type = output_type
        self.tool_name = tool["function"]["name"]
        self.runner = prompt | model.bind_tools([tool], tool_choice=tool_choice)

    async def ainvoke(self, values: dict[str, Any]) -> dict[str, Any]:
        message = await self.runner.ainvoke(values)
        parsed, error = _parse_message(message, self.output_type, self.tool_name)
        return {"parsed": parsed, "raw": message, "parsing_error": error}


def _resolve_tool_choice(value: str | None, tool_name: str) -> Any:
    """把配置里的字符串映射成 ``tool_choice`` 参数。

    默认 ``auto``：思考模式模型不支持强制指定具体函数。只有显式把配置写成
    工具名时才强制调用该工具（非思考模型可以用它提高字段遵循度）。
    """

    normalized = (value or "auto").strip()
    if normalized in _PASSTHROUGH_TOOL_CHOICES:
        return normalized
    if normalized == tool_name:
        return {"type": "function", "function": {"name": tool_name}}
    logger.warning(
        "stage=llm_config tool_choice=%s 不受支持，回退为 auto（可选：%s 或 %s）",
        normalized,
        "auto",
        tool_name,
    )
    return "auto"


def _parse_message(
    message: Any, output_type: type[BaseModel], tool_name: str
) -> tuple[BaseModel | None, Exception | None]:
    """优先取工具调用参数，其次回退解析正文 JSON。"""

    error: Exception | None = None
    calls = getattr(message, "tool_calls", None) or []

    if calls:
        call = next(
            (item for item in calls if item.get("name") == tool_name), calls[0]
        )
        args = call.get("args")
        if isinstance(args, str):
            # 个别端点把参数当字符串返回，这里再解一次。
            try:
                args = json.loads(args)
            except json.JSONDecodeError as exc:
                error = exc
                args = None
        if isinstance(args, dict):
            try:
                return output_type.model_validate(args), None
            except ValidationError as exc:
                error = exc
        elif error is None:
            error = ValueError(f"工具 {tool_name} 的参数不是 JSON 对象：{_preview(args)}")

    payload = _extract_json_object(_content_text(getattr(message, "content", "")))
    if payload is not None:
        try:
            return output_type.model_validate(payload), None
        except ValidationError as exc:
            return None, exc

    if error is None:
        error = ValueError(f"模型未调用工具 {tool_name}，正文中也没有可解析的 JSON")
    return None, error


def _content_text(content: Any) -> str:
    """把 AIMessage.content（字符串或多段内容块）压成纯文本。"""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "\n".join(parts)
    return ""


def _extract_json_object(text: str) -> dict[str, Any] | None:
    """从可能带 Markdown 代码块的文本里取出第一个 JSON 对象。"""

    if not text or not text.strip():
        return None

    candidates = [text.strip()]
    candidates.extend(re.findall(r"```(?:json)?\s*(.+?)```", text, re.DOTALL))
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def _preview(value: Any, limit: int = 300) -> str:
    compact = " ".join(str(value).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."
