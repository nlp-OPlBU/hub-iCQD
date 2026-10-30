"""模型提取视觉事实，Python 统一执行风险分级规则。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

RiskLevel = Literal["high", "medium", "low", "none", "non_corridor"]


class Observation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    scene: Literal["corridor", "non_corridor", "uncertain"]
    image_quality: Literal["clear", "insufficient"]
    electric_vehicle: bool
    battery: bool
    flying_wire_charging: bool
    flame_or_smoke: bool
    clutter: Literal["none", "minor", "major", "uncertain"]
    combustibles: Literal["none", "small", "large", "uncertain"]
    evidence: list[str] = Field(min_length=1, max_length=12)
    uncertainties: list[str] = Field(max_length=12)


class Assessment(BaseModel):
    status: Literal["ok", "needs_review"]
    risk_level: RiskLevel | None
    risk_label: str
    reason: str
    actions: list[str]
    observations: Observation


def classify(obs: Observation) -> Assessment:
    """优先级：有效非楼道 → 已见起火因素 → 中 → 低 → 无；不确定时复核。"""
    def result(level, label, reason, actions, status="ok"):
        return Assessment(status=status, risk_level=level, risk_label=label,
                          reason=reason, actions=actions, observations=obs)

    if obs.scene == "uncertain":
        return result(None, "待复核", "无法确认是否为楼道场景。", ["补拍楼道全景，包含地面和通行区域。"], "needs_review")
    if obs.scene == "non_corridor" and obs.image_quality == "clear":
        return result("non_corridor", "非楼道", "图片内容与楼道场景无关。", ["请拍摄需要检查的楼道。"])
    if obs.scene == "corridor" and any((obs.electric_vehicle, obs.battery,
                                       obs.flying_wire_charging, obs.flame_or_smoke)):
        hazards = [name for flag, name in ((obs.electric_vehicle, "电动车"), (obs.battery, "电瓶"),
                   (obs.flying_wire_charging, "飞线充电"), (obs.flame_or_smoke, "明火或烟雾")) if flag]
        return result("high", "高风险", "楼道内观察到：" + "、".join(hazards) + "。",
                      ["立即通知物业或管理人员核实处理。", "发现烟火时远离危险区域，及时报警。"])
    if obs.image_quality == "insufficient":
        return result(None, "待复核", "图片过暗、模糊或遮挡，不能据此排除隐患。",
                      ["改善光线并补拍清晰照片。"], "needs_review")
    if obs.clutter == "major" or obs.combustibles == "large":
        return result("medium", "中风险", "大量堆积物严重影响通行，或有大量可燃物堆放。",
                      ["尽快清理堆物，恢复疏散通道。", "通知物业核实可燃物堆放情况。"])
    if obs.clutter == "uncertain" or obs.combustibles == "uncertain":
        return result(None, "待复核", "堆物规模或可燃物情况不明确。",
                      ["补拍堆放物和剩余通行区域，进行人工复核。"], "needs_review")
    if obs.clutter == "minor" or obs.combustibles == "small":
        return result("low", "低风险", "存在少量堆物，尚未观察到严重堵塞。", ["及时移走堆放物，保持楼道畅通。"])
    return result("none", "无风险", "本张照片可见范围内楼道干净，未见堆放物。",
                  ["继续保持通道畅通；照片结论仅覆盖可见范围。"])
