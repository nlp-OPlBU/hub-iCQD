import json
from typing import Literal

from agents import Agent, ModelSettings, Runner
from pydantic import BaseModel, Field

from .config import get_settings
from .model_config import build_model


class SentimentResult(BaseModel):
    """进程 1 的结构化情感识别结果。"""

    sentiment: Literal["positive", "negative"] = Field(
        description="情感倾向：正向为 positive，负面为 negative"
    )
    reason: str = Field(description="判断该情感倾向的简短原因说明")


_SYSTEM_PROMPT = (
    "你是一个专业的情感识别助手。"
    "请判断用户输入文本的情感倾向，只能归类为 positive（正向/积极/平稳）或 negative（负面/消极/低落），"
    "并用简短的中文说明判断原因。"
)


async def analyze_sentiment(user_input: str) -> SentimentResult:
    """
    进程 1：接受用户请求，使用 OpenAI Agents SDK 进行情感识别。
    优先使用结构化 output_type；若底层兼容模型不支持 json_schema，则自动降级为 JSON 解析。
    """
    model = build_model()
    settings = get_settings()

    structured_agent = Agent(
        name="SentimentClassifier",
        instructions=_SYSTEM_PROMPT,
        model=model,
        model_settings=ModelSettings(temperature=settings.sentiment_temperature),
        output_type=SentimentResult,
    )

    try:
        result = await Runner.run(structured_agent, user_input)
        if isinstance(result.final_output, SentimentResult):
            return result.final_output
    except Exception:
        pass

    # 兼容不支持结构化 output_type (json_schema) 的 OpenAI 兼容接口
    fallback_agent = Agent(
        name="SentimentClassifierFallback",
        instructions=(
            _SYSTEM_PROMPT
            + ' 请严格返回 JSON 格式：{"sentiment": "positive" | "negative", "reason": "..."}'
        ),
        model=model,
        model_settings=ModelSettings(temperature=settings.sentiment_temperature),
    )
    fallback_result = await Runner.run(fallback_agent, user_input)
    content = str(fallback_result.final_output).strip()
    if content.startswith("```"):
        content = content.strip("`")
        if content.startswith("json"):
            content = content[4:].strip()
    data = json.loads(content)
    return SentimentResult(**data)
