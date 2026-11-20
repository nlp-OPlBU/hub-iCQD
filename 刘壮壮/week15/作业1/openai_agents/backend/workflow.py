from typing import Literal
from typing_extensions import TypedDict

from .response import generate_comfort, generate_encouragement
from .sentiment import analyze_sentiment


class SentimentWorkflowResult(TypedDict):
    """OpenAI Agents 工作流输出状态，串联进程 1 与进程 2。"""

    user_input: str
    sentiment: Literal["positive", "negative"]
    sentiment_reason: str
    reply: str


async def run_sentiment_workflow(user_input: str) -> SentimentWorkflowResult:
    """
    串联进程 1（情感识别）与进程 2（正向鼓励 / 负面排解）。
    """
    # 进程 1：情感识别
    sentiment_result = await analyze_sentiment(user_input)

    # 进程 2：根据识别结果路由至对应 Agent
    if sentiment_result.sentiment == "positive":
        reply = await generate_encouragement(
            user_input=user_input,
            sentiment_reason=sentiment_result.reason,
        )
    else:
        reply = await generate_comfort(
            user_input=user_input,
            sentiment_reason=sentiment_result.reason,
        )

    return {
        "user_input": user_input,
        "sentiment": sentiment_result.sentiment,
        "sentiment_reason": sentiment_result.reason,
        "reply": reply,
    }
