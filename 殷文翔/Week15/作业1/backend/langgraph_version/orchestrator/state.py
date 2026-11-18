from pydantic import BaseModel
from ..config import AnalyzeResult


class ChatState(BaseModel):
    user_input: str  # 用户原始输入
    classifier_result: AnalyzeResult = None  # 对用户的情感分类结果
    response_text: str = None  # 对用户的回复
