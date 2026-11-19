from functools import cache
from typing import NotRequired, TypedDict

from langgraph.graph import END, START, StateGraph
from openai import OpenAI

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


# 图从输入文本开始，识别节点和回应节点依次把结果写回状态。
class State(TypedDict):
    text: str
    sentiment: NotRequired[Sentiment]
    reply: NotRequired[str]


@cache
def client() -> OpenAI:
    return OpenAI(timeout=30, max_retries=0)


def messages(prompt: str, text: str) -> list:
    return [{"role": "system", "content": prompt}, {"role": "user", "content": text}]


def classify(text: str) -> Sentiment:
    # 按 Classification 的结构解析模型输出，供条件边直接读取。
    result = client().chat.completions.parse(
        model=model_name(),
        messages=messages(CLASSIFY_PROMPT, text),
        response_format=Classification,
    )
    parsed = result.choices[0].message.parsed
    if parsed is None:
        raise ValueError("模型未返回情感分类")
    return parsed.sentiment


def respond(text: str, sentiment: Sentiment) -> str:
    result = client().chat.completions.create(
        model=model_name(),
        messages=messages(REPLY_PROMPTS[sentiment], text),
    )
    return require_reply(result.choices[0].message.content)


def build_flow(classifier_pool, responder_pool):
    async def identify(state: State) -> dict:
        sentiment = await run_stage(classifier_pool, classify, state["text"])
        return {"sentiment": sentiment}

    async def reply(state: State, sentiment: Sentiment) -> dict:
        text = await run_stage(responder_pool, respond, state["text"], sentiment)
        return {"reply": text}

    async def encourage(state: State) -> dict:
        return await reply(state, Sentiment.POSITIVE)

    async def comfort(state: State) -> dict:
        return await reply(state, Sentiment.NEGATIVE)

    graph = StateGraph(State)
    graph.add_node("identify", identify)
    graph.add_node("encourage", encourage)
    graph.add_node("comfort", comfort)
    graph.add_edge(START, "identify")
    # 根据识别标签选择鼓励或排解节点，每次请求只走其中一支。
    graph.add_conditional_edges(
        "identify",
        lambda state: state["sentiment"],
        {Sentiment.POSITIVE: "encourage", Sentiment.NEGATIVE: "comfort"},
    )
    graph.add_edge("encourage", END)
    graph.add_edge("comfort", END)
    # 服务启动时编译一次，每次请求传入自己的初始状态。
    workflow = graph.compile()

    async def run(text: str) -> Reply:
        result = await workflow.ainvoke({"text": text})
        return Reply(sentiment=result["sentiment"], reply=result["reply"])

    return run
