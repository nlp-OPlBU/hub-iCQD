from functools import lru_cache
from typing import Annotated, Literal

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    Runner,
    StopAtTools,
    function_tool,
    set_tracing_disabled,
)
from openai import AsyncOpenAI
from pydantic import Field, ValidationError

from .shared import (
    API_KEY,
    BASE_URL,
    MODEL,
    REPLY_PROMPTS,
    STRATEGY_BY_LABEL,
    ChatResponse,
    MissingConfigError,
    SentimentResult,
    parse_sentiment_value,
)

BACKEND_NAME = "agents"

# 本作业不需要 OpenAI Tracing；关闭后可避免额外要求 tracing 服务
set_tracing_disabled(True)


# ---------------------------------------------------------------------------
# 进程 1：情感识别工具与 Agent
# ---------------------------------------------------------------------------
@function_tool
def report_sentiment(
    label: Annotated[
        Literal["positive", "negative", "neutral"],
        Field(description="情感倾向：positive 正向 / negative 负面 / neutral 中性"),
    ],
    emotion: Annotated[str, Field(description="具体情绪，如开心、焦虑、愤怒、平静")],
    confidence: Annotated[float, Field(description="0~1 之间的置信度")],
    reason: Annotated[str, Field(description="不超过 40 字的判断依据")],
) -> str:
    """提交对用户消息的情感识别结果。每次识别必须且只能调用一次本工具，不要直接回答用户。"""
    result = SentimentResult(
        label=label,
        emotion=emotion.strip() or "平静",
        confidence=max(0.0, min(1.0, float(confidence))),
        reason=reason.strip() or "模型未给出判断依据",
    )
    return result.model_dump_json()


SENTIMENT_INSTRUCTIONS = """你是「情感识别进程」，只负责判断用户消息的情感倾向。
判断标准：
- positive：开心、感激、期待、满意、兴奋等正向情绪；
- negative：难过、焦虑、愤怒、失望、孤独、压力等负面情绪；
- neutral：客观陈述、信息询问，或没有明显情绪色彩。
必须调用 report_sentiment 工具提交结果，不要直接回答用户，也不要输出多余的解释。"""


def _build_model() -> OpenAIChatCompletionsModel:
    if not API_KEY:
        raise MissingConfigError(
            "未配置 OPENAI_API_KEY（或 DEEPSEEK_API_KEY），请在 .env 中填写后重启后端。"
        )
    client = AsyncOpenAI(
        api_key=API_KEY,
        base_url=BASE_URL,
        timeout=90.0,
        max_retries=2,
    )
    return OpenAIChatCompletionsModel(model=MODEL, openai_client=client)


@lru_cache(maxsize=1)
def get_agents() -> dict[str, Agent]:
    """创建四个 Agent：1 个情感识别 Agent + 3 个回复 Agent。"""
    model = _build_model()
    common_settings = ModelSettings(temperature=0.8)

    sentiment_agent = Agent(
        name="情感识别进程",
        instructions=SENTIMENT_INSTRUCTIONS,
        tools=[report_sentiment],
        tool_use_behavior=StopAtTools(stop_at_tool_names=["report_sentiment"]),
        model=model,
        model_settings=ModelSettings(temperature=0.0),
    )
    encourage_agent = Agent(
        name="鼓励进程",
        instructions=REPLY_PROMPTS["encourage"],
        model=model,
        model_settings=common_settings,
    )
    comfort_agent = Agent(
        name="排解进程",
        instructions=REPLY_PROMPTS["comfort"],
        model=model,
        model_settings=common_settings,
    )
    guide_agent = Agent(
        name="引导进程",
        instructions=REPLY_PROMPTS["guide"],
        model=model,
        model_settings=common_settings,
    )
    return {
        "sentiment": sentiment_agent,
        "encourage": encourage_agent,
        "comfort": comfort_agent,
        "guide": guide_agent,
    }


# ---------------------------------------------------------------------------
# 串联进程 1 和进程 2
# ---------------------------------------------------------------------------
async def extract_sentiment(agent: Agent, message: str) -> SentimentResult:
    """进程 1：运行情感识别 Agent，拿到结构化结果。"""
    last_error: Exception | None = None
    for attempt in range(2):
        prompt = message
        if attempt == 1:
            prompt = (
                "上一次没有得到有效结果。请立刻调用 report_sentiment 工具提交情感识别结果，"
                f"不要输出其他内容。\n用户消息：{message}"
            )
        result = await Runner.run(agent, input=prompt, max_turns=3)
        try:
            return parse_sentiment_value(result.final_output)
        except (ValueError, ValidationError) as exc:
            last_error = exc
    raise RuntimeError(f"情感识别进程没有返回可解析的结果：{last_error}")


async def run(message: str) -> ChatResponse:
    """后端调用入口：进程 1 -> 选择进程 2 -> 返回结果。"""
    agents = get_agents()

    sentiment = await extract_sentiment(agents["sentiment"], message)
    strategy = STRATEGY_BY_LABEL[sentiment.label]

    reply_result = await Runner.run(agents[strategy], input=message, max_turns=3)
    reply = str(reply_result.final_output or "").strip()
    if not reply:
        raise RuntimeError("回复生成进程返回了空内容")

    return ChatResponse(
        backend=BACKEND_NAME,
        model=MODEL,
        sentiment=sentiment,
        strategy=strategy,
        reply=reply,
    )
