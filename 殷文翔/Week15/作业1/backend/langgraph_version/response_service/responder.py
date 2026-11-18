from langchain.agents import create_agent

from langchain_openai import ChatOpenAI
from ..config import get_settings

settings = get_settings()

model = ChatOpenAI(
    model=settings.default_model,  # 模型的代号
    base_url=settings.openai_base_url,
    api_key=settings.openai_api_key,
)

responder_encourage_agent = create_agent(
    name="responder_encourage_agent",
    model=model,
    system_prompt="""
    你是一位"知心大姐姐",现在用户似乎心情不错，请你鼓励鼓励他。输出最终需要对用户说的话。""",
)

responder_relieve_agent = create_agent(
    name="responder_relieve_agent",
    model=model,
    system_prompt="""
    你是一位"知心大姐姐",现在用户似乎遇到些不开心的事情，请你排解排解他。输出最终需要对用户说的话。""",
)
