from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_settings
from .model_config import get_llm


def generate_encouragement(user_input: str, sentiment_reason: str = "") -> str:
    """进程 2（正向分支）：对正向情绪给予真诚的鼓励与肯定。"""
    settings = get_settings()
    llm = get_llm(temperature=settings.response_temperature)
    system_prompt = (
        "你是一个温暖、充满正能量的伙伴。"
        "用户当前的情绪是正向积极的，请结合用户说的话给予真诚的肯定与鼓励，"
        "语气自然亲切，字数控制在 100 字以内。"
    )
    user_prompt = f"用户输入：{user_input}\n情感分析：{sentiment_reason}"
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content).strip()


def generate_comfort(user_input: str, sentiment_reason: str = "") -> str:
    """进程 2（负面分支）：对负面情绪给予共情、安慰与排解建议。"""
    settings = get_settings()
    llm = get_llm(temperature=settings.response_temperature)
    system_prompt = (
        "你是一个温柔、善解人意的心理支持伙伴。"
        "用户当前的情绪偏负面或低落，请先真诚共情和安抚情绪，再给出一条温和、可行的排解小建议，"
        "不要说教，字数控制在 120 字以内。"
    )
    user_prompt = f"用户输入：{user_input}\n情感分析：{sentiment_reason}"
    response = llm.invoke(
        [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]
    )
    return str(response.content).strip()
