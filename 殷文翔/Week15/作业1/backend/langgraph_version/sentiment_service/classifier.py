from langchain.agents import create_agent

from langchain_openai import ChatOpenAI
from ..config import get_settings

settings = get_settings()

model = ChatOpenAI(
    model=settings.default_model,  # 模型的代号
    base_url=settings.openai_base_url,
    api_key=settings.openai_api_key,
    extra_body={"response_format": {"type": "json_object"}}
)

classifier_agent = create_agent(
    name="classifier",
    model=model,
    system_prompt="""
    你是一个情感分析专家，请你根据用户的输入判断用户现在的情感是正向的还是负面的。
    只返回一个 JSON 对象，不要添加解释或 Markdown 代码块。
    正向情感返回 {"result": "positive"}，负面情感返回 {"result": "negative"}。""",
)
