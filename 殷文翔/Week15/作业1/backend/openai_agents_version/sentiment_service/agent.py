"""情感分析 agent """
from agents import Agent, ModelSettings, OpenAIChatCompletionsModel, Runner, set_tracing_disabled
from openai import AsyncOpenAI

from ..config import get_settings

config = get_settings()
client = AsyncOpenAI(api_key=config.openai_api_key, base_url=config.openai_base_url)
set_tracing_disabled(True)

sentiment_analysis_agent = Agent(
    name='sentiment_analysis_agent',
    model=OpenAIChatCompletionsModel(
        model=config.default_model,
        openai_client=client,
    ),
    instructions="""
    你是一个情感分析专家，请你根据用户的输入判断用户现在的情感是正向的还是负面的。
    只返回一个 JSON 对象，不要添加解释或 Markdown 代码块。
    正向情感返回 {"result": "positive"}，负面情感返回 {"result": "negative"}。
    """,
    # 当前接口不支持 output_type 生成的 json_schema，使用 JSON 模式后在本地校验。
    model_settings=ModelSettings(
        extra_body={"response_format": {"type": "json_object"}},
    ),
)
