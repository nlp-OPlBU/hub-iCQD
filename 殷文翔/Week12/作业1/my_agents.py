from agents import Agent, AsyncOpenAI, OpenAIResponsesModel
from agents import set_tracing_disabled
from models import Result
import os


client = AsyncOpenAI(api_key=os.environ['DEEPSEEK_API_KEY'], base_url=os.environ['DeepSeek_URL'])
set_tracing_disabled(True)
security_analysis_agent = Agent(
    name="Security Analysis Agent",
    model=OpenAIResponsesModel(
        model="deepseek-flash",
        openai_client=client
    ),
    instructions=("""
    你是一个判断楼道消防安全的助手，你的任务是解析上传的图片判断楼道是否安全并给出安全等级和图片分析描述。
安全等级判断标准：
高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
中风险：楼道中存在大量堆积物严重影响通行或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
低风险：存在楼道堆物现象但不严重。
无风险：楼道干净，无堆放物品。
非楼道：与楼道无关的图片。

输出格式要求：
只返回一个合法 JSON 对象，必须包含 classification 和 description 两个字段。
classification 只能是：高风险、中风险、低风险、无风险、非楼道。
description 必须是非空字符串，说明照片中可见的依据。
不要使用 Markdown 代码块，不要在 JSON 前后添加解释文字，不要输出思考过程。
格式示例（仅表示字段格式，具体等级必须根据当前图片判断）：
{"classification":"非楼道","description":"图片为非楼道场景。"}
    """),
    output_type=Result
)
