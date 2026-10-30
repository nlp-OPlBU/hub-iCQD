# -*- coding: utf-8 -*-
"""响应数据结构（Pydantic 模型）。"""

from typing import List, Optional

from pydantic import BaseModel, Field


class Hazard(BaseModel):
    """单个隐患元素。"""

    type: str = "其他"
    position: str = ""
    flammable: str = "不确定"
    blocks_passage: bool = False
    ignition_risk: str = ""
    severity: str = "low"


class Scene(BaseModel):
    is_corridor: bool = True
    desc: str = ""


class AnalyzeResult(BaseModel):
    """接口返回体。"""

    ok: bool = True
    risk_level: str = "none"
    risk_label: str = "无风险"
    scene: Scene = Field(default_factory=Scene)
    hazards: List[Hazard] = Field(default_factory=list)
    reasons: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: float = 0.0
    uncertain: List[str] = Field(default_factory=list)

    model_raw_level: str = ""
    corrected: bool = False
    parse_error: bool = False
    cached: bool = False
    model: str = ""
    latency_ms: int = 0
    tokens: Optional[int] = None
    request_id: str = ""
