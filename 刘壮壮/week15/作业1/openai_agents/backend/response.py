from agents import Agent, ModelSettings, Runner

from .config import get_settings
from .model_config import build_model


async def generate_encouragement(user_input: str, sentiment_reason: str = "") -> str:
    """进程 2（正向分支）：使用鼓励 Agent 对正向情绪给予真诚的肯定与鼓励。"""
    settings = get_settings()
    agent = Agent(
        name="EncouragementAgent",
        instructions=(
            "你是一个温暖、充满正能量的伙伴。"
            "用户当前的情绪是正向积极的，请结合用户说的话给予真诚的肯定与鼓励，"
            "语气自然亲切，字数控制在 100 字以内。"
        ),
        model=build_model(),
        model_settings=ModelSettings(temperature=settings.response_temperature),
    )
    user_prompt = f"用户输入：{user_input}\n情感分析：{sentiment_reason}"
    result = await Runner.run(agent, user_prompt)
    return str(result.final_output).strip()


async def generate_comfort(user_input: str, sentiment_reason: str = "") -> str:
    """进程 2（负面分支）：使用排解 Agent 对负面情绪给予共情、安慰与排解建议。"""
    settings = get_settings()
    agent = Agent(
        name="ComfortAgent",
        instructions=(
            "你是一个温柔、善解人意的心理支持伙伴。"
            "用户当前的情绪偏负面或低落，请先真诚共情和安抚情绪，再给出一条温和、可行的排解小建议，"
            "不要说教，字数控制在 120 字以内。"
        ),
        model=build_model(),
        model_settings=ModelSettings(temperature=settings.response_temperature),
    )
    user_prompt = f"用户输入：{user_input}\n情感分析：{sentiment_reason}"
    result = await Runner.run(agent, user_prompt)
    return str(result.final_output).strip()
