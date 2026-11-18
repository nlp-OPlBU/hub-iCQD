"""情感分析 agent """
from agents import Agent,  OpenAIChatCompletionsModel,  set_tracing_disabled
from openai import AsyncOpenAI
from ..config import get_settings

config = get_settings()
client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
set_tracing_disabled(True)



encourage_agent = Agent(
    name='encourage_agent',
    model=OpenAIChatCompletionsModel(
        model=config.default_model,
        openai_client=client,
    ),
    instructions="""
    你是一位"知心大姐姐",现在用户似乎心情不错，请你鼓励鼓励他。输出最终需要对用户说的话。
    """,
)


relieve_agent = Agent(
    name='relieve _agent',
    model=OpenAIChatCompletionsModel(
        model=config.default_model,
        openai_client=client,
    ),
    instructions="""
    你是一位"知心大姐姐",现在用户似乎遇到些不开心的事情，请你排解排解他。输出最终需要对用户说的话。
    """,
)
