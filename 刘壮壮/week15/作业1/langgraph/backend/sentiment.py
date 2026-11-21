import json
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .config import get_settings
from .model_config import get_llm


class SentimentResult(BaseModel):
    """进程 1 的结构化情感识别结果。"""

    sentiment: Literal["positive", "negative"] = Field(
        description="情感倾向：正向为 positive，负面为 negative"
    )
    reason: str = Field(description="判断该情感倾向的简短原因说明")


def analyze_sentiment(user_input: str) -> SentimentResult:
    """
    进程 1：接受用户请求，进行情感识别。
    """
    settings = get_settings()
    llm = get_llm(temperature=settings.sentiment_temperature)
    system_prompt = (
        "你是一个专业的情感识别助手。"
        "请判断用户输入文本的情感倾向，只能归类为 positive（正向/积极/平稳）或 negative（负面/消极/低落），"
        "并用简短的中文说明判断原因。"
    )

    try:
        structured_llm = llm.with_structured_output(SentimentResult)
        result = structured_llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_input),
            ]
        )
        if isinstance(result, SentimentResult):
            return result
    except Exception:
        pass

    # 兼容不支持 with_structured_output 的 OpenAI 兼容接口
    raw_msg = llm.invoke(
        [
            SystemMessage(
                content=(
                    system_prompt
                    + ' 请严格返回 JSON 格式：{"sentiment": "positive" | "negative", "reason": "..."}'
                )
            ),
            HumanMessage(content=user_input),
        ]
    )
    content = str(raw_msg.content).strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)
    return SentimentResult(**data)
