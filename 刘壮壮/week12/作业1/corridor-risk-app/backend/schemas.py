from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field


class RiskLevel(str, Enum):
    NOT_CORRIDOR = "非楼道"
    NONE = "无隐患"
    LOW = "低风险"
    MEDIUM = "中等风险"
    HIGH = "高风险"


class SceneEvidence(BaseModel):
    """模型提取的视觉事实；最终风险等级由后端规则计算。"""

    is_corridor: bool
    corridor_confidence: float = Field(ge=0, le=1)
    high_risk_elements: list[str] = Field(default_factory=list)
    clutter_level: Literal["none", "light", "heavy"]
    combustible_load: Literal["none", "light", "heavy"]
    passage_blocked: bool
    observations: list[str] = Field(default_factory=list, max_length=8)
    scene_summary: str = Field(min_length=1, max_length=300)
    confidence: float = Field(ge=0, le=1)


class AnalysisResponse(BaseModel):
    risk_level: RiskLevel
    confidence: float = Field(ge=0, le=1)
    scene_summary: str
    evidence: list[str]
    recommendation: str
    request_id: str
