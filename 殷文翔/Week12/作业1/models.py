from pydantic import BaseModel,Field
from typing import Literal


class Result(BaseModel):
    """照片解析结果"""
    classification: Literal["高风险","中风险","低风险","无风险","非楼道"] = Field(description="安全等级")
    description:str = Field(description="对照片的分析")