from functools import cache

from agents import Agent, OpenAIChatCompletionsModel, RunConfig, Runner
from openai import AsyncOpenAI

from .shared import (
    CLASSIFY_PROMPT,
    REPLY_PROMPTS,
    Classification,
    Reply,
    Sentiment,
    model_name,
    require_reply,
    run_stage,
)

# 关闭 SDK 链路追踪，避免将用户文本写入追踪数据。
RUN_CONFIG = RunConfig(tracing_disabled=True)


@cache
def model() -> OpenAIChatCompletionsModel:
    return OpenAIChatCompletionsModel(
        model=model_name(),
        openai_client=AsyncOpenAI(timeout=30, max_retries=0),
    )


def classify(text: str) -> Sentiment:
    agent = Agent(
        name="Emotion classifier",
        instructions=CLASSIFY_PROMPT,
        model=model(),
        # 让 SDK 将分类结果解析为结构化对象，后续可直接读取标签。
        output_type=Classification,
    )
    result = Runner.run_sync(agent, text, max_turns=1, run_config=RUN_CONFIG)
    return result.final_output_as(Classification, raise_if_incorrect_type=True).sentiment


def respond(text: str, sentiment: Sentiment) -> str:
    agent = Agent(
        name="Emotion responder",
        # 后端给出已选定的情绪标签，由标签决定回应提示词。
        instructions=REPLY_PROMPTS[sentiment],
        model=model(),
    )
    result = Runner.run_sync(agent, text, max_turns=1, run_config=RUN_CONFIG)
    return require_reply(result.final_output_as(str, raise_if_incorrect_type=True))


def build_flow(classifier_pool, responder_pool):
    async def run(text: str) -> Reply:
        # 先等待识别 Agent 返回标签，再把原文和标签交给回应 Agent。
        sentiment = await run_stage(classifier_pool, classify, text)
        reply = await run_stage(responder_pool, respond, text, sentiment)
        return Reply(sentiment=sentiment, reply=reply)

    return run
