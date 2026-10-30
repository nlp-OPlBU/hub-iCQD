"""大模型客户端封装（基于 LangChain）。

默认使用 DashScope（阿里云百炼）的 OpenAI 兼容接口，
可以通过环境变量覆盖：

    OPENAI_API_KEY   API Key
    OPENAI_BASE_URL  接口地址
    SQL_AGENT_MODEL  模型名称（默认 qwen-max）

内部使用 langchain-openai 的 ChatOpenAI 作为统一调用入口，
对外保持 chat / chat_json 两个高层方法，与上层 Agent 解耦。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-max"


def _to_langchain_messages(messages: list[dict[str, str]]) -> list[BaseMessage]:
    """把 OpenAI 格式的消息列表转换为 LangChain 的 BaseMessage 列表。

    LangChain 的 ChatOpenAI 虽然也能接受 dict 列表，但显式做一层转换后
    可以在 role 异常、字段缺失时给出更友好的错误，行为也更稳定。
    """
    mapping = {
        "system": SystemMessage,
        "assistant": AIMessage,
        "user": HumanMessage,
    }
    result: list[BaseMessage] = []
    for idx, msg in enumerate(messages):
        role = msg.get("role", "user").lower()
        content = msg.get("content", "")
        cls = mapping.get(role, HumanMessage)
        if not isinstance(content, str):
            content = str(content)
        result.append(cls(content=content))
        # idx 未使用，只是为了将来调试方便
        _ = idx
    return result


class LLMClient:
    """基于 LangChain ChatOpenAI 的轻量封装，带重试与 JSON 解析。"""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")
        if not self.api_key:
            raise ValueError(
                "未配置大模型 API Key，请先设置环境变量 OPENAI_API_KEY"
                "（DashScope/阿里云百炼：https://bailian.console.aliyun.com/）"
            )
        self.base_url = base_url or os.getenv("OPENAI_BASE_URL") or DEFAULT_BASE_URL
        self.model = model or os.getenv("SQL_AGENT_MODEL") or DEFAULT_MODEL
        self.temperature = temperature
        self.max_retries = max_retries

        # LangChain ChatOpenAI 天然支持 OpenAI 兼容端点，只需传入 base_url
        self.llm = ChatOpenAI(
            model=self.model,
            api_key=self.api_key,
            base_url=self.base_url,
            temperature=self.temperature,
            timeout=timeout,
            max_retries=0,  # 我们自己控制重试与退避
        )

    # ------------------------------------------------------------------ 调用
    def chat(
        self,
        messages: list[dict[str, str]],
        temperature: float | None = None,
    ) -> str:
        """调用大模型并返回文本内容，失败时按指数退避重试。"""
        lc_msgs = _to_langchain_messages(messages)
        effective_temp = (
            self.temperature if temperature is None else temperature
        )

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                # invoke() 接受 BaseMessage 列表，返回 AIMessage
                llm = (
                    self.llm
                    if temperature is None
                    else self.llm.bind(temperature=effective_temp)
                )
                response: AIMessage = llm.invoke(lc_msgs)
                content = response.content
                if content is None or content == "":
                    raise ValueError("模型返回内容为空")
                if not isinstance(content, str):
                    # 极少数场景 content 可能是 list（多模态分段输出），兜底转 str
                    content = str(content)
                return content.strip()
            except Exception as exc:  # noqa: BLE001 - 需要统一重试
                last_error = exc
                msg = str(exc).lower()
                # 鉴权错误直接抛出，不进入重试
                if "401" in msg or "authentication" in msg or "api key" in msg:
                    logger.error(
                        "大模型 API Key 鉴权失败。请在环境变量 OPENAI_API_KEY 中"
                        "配置可用的 DashScope Key（阿里云百炼 https://bailian.console.aliyun.com/）。"
                    )
                    raise RuntimeError(
                        "大模型 API Key 无效或已过期，请通过环境变量 OPENAI_API_KEY 配置正确的"
                        " DashScope API Key。"
                    ) from exc
                logger.warning(
                    "调用大模型失败（第 %s/%s 次）：%s",
                    attempt,
                    self.max_retries,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(min(2 ** attempt, 8))
        raise RuntimeError(f"调用大模型失败：{last_error}") from last_error

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """要求模型输出 JSON，并做容错解析。"""
        text = self.chat(messages)
        return parse_json(text)


def parse_json(text: str) -> dict[str, Any]:
    """从模型输出中提取 JSON 对象，兼容 ```json 围栏和前后解释文字。"""
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", candidate, flags=re.S | re.I)
    if fence:
        candidate = fence.group(1).strip()

    try:
        data = json.loads(candidate)
        return data if isinstance(data, dict) else {"value": data}
    except json.JSONDecodeError:
        pass

    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        try:
            data = json.loads(candidate[start : end + 1])
            return data if isinstance(data, dict) else {"value": data}
        except json.JSONDecodeError:
            logger.debug("JSON 解析失败，原始输出：%s", text)

    raise ValueError(f"无法解析模型输出的 JSON：{text[:200]}")
