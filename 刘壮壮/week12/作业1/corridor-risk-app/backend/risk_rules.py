from backend.schemas import RiskLevel, SceneEvidence


RECOMMENDATIONS: dict[RiskLevel, str] = {
    RiskLevel.NOT_CORRIDOR: "请重新拍摄楼道、楼梯间或疏散通道的完整场景。",
    RiskLevel.NONE: "当前未发现明显隐患，请继续保持通道整洁畅通。",
    RiskLevel.LOW: "建议及时移除零散堆物，避免物品持续增加或占用通道。",
    RiskLevel.MEDIUM: "请尽快清理堵塞物和可燃堆积物，恢复疏散通道宽度。",
    RiskLevel.HIGH: "请立即停止充电并移走电动车或电瓶，必要时联系物业和消防管理人员。",
}


def classify_risk(scene: SceneEvidence) -> RiskLevel:
    """按业务优先级对模型提取的事实做确定性分类。"""
    if not scene.is_corridor:
        return RiskLevel.NOT_CORRIDOR
    if scene.high_risk_elements:
        return RiskLevel.HIGH
    if (
        scene.passage_blocked
        or scene.clutter_level == "heavy"
        or scene.combustible_load == "heavy"
    ):
        return RiskLevel.MEDIUM
    if scene.clutter_level == "light" or scene.combustible_load == "light":
        return RiskLevel.LOW
    return RiskLevel.NONE


def build_evidence(scene: SceneEvidence, level: RiskLevel) -> list[str]:
    if level == RiskLevel.NOT_CORRIDOR:
        return scene.observations or ["画面未呈现可确认的楼道或疏散通道"]

    evidence = list(scene.observations)
    if scene.high_risk_elements:
        evidence.extend(f"发现高危元素：{item}" for item in scene.high_risk_elements)
    if scene.passage_blocked:
        evidence.append("通行或疏散空间受到明显影响")
    if scene.clutter_level == "heavy":
        evidence.append("存在大量堆积物")
    if scene.combustible_load == "heavy":
        evidence.append("存在大量可燃物")
    if not evidence:
        evidence.append("楼道整洁，未发现明显堆放物品")

    # 去重并保持模型观察顺序，避免界面信息过载。
    return list(dict.fromkeys(evidence))[:6]
