# -*- coding: utf-8 -*-
"""风险等级校正规则。

模型负责"看懂图"，等级由确定性规则兜底校正：
- 明确出现电动车 / 电瓶 / 飞线充电等元素 -> 强制高风险；
- 模型把电动车类元素判低了 -> 强制升级；
- 模型判为 medimum/low 但存在明显堵塞且可燃的大量堆积 -> 至少中等风险；
- 非楼道场景 -> 强制 not_corridor。
只升不降（除非楼道外），避免把模型看到的真实风险人为压低。
"""

from typing import List, Tuple

from .schema import Hazard

LEVEL_ORDER = {"none": 0, "low": 1, "medium": 2, "high": 3}
LEVEL_LABEL = {
    "none": "无风险",
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
    "not_corridor": "非楼道",
}

# 出现即判高风险的强特征（对应作业定义：电动车、电瓶、飞线充电）
HIGH_KEYWORDS = ("电动车", "电瓶", "电池", "飞线", "私拉", "充电器", "充电线", "插排")

# 隐患元素类型白名单：模型给的自由文本先归一到这里，无法归类统一为"其他"
HAZARD_TYPES = (
    "电动车",
    "电瓶",
    "飞线充电",
    "充电器",
    "纸箱",
    "木质家具",
    "泡沫",
    "布料",
    "液化气罐",
    "明火",
    "其他",
)

TYPE_KEYWORDS = (
    ("电动车", ("电动车", "电瓶车", "电动自行车")),
    ("电瓶", ("电瓶", "电池")),
    ("飞线充电", ("飞线", "私拉", "充电线")),
    ("充电器", ("充电器", "充电机", "插排")),
    ("纸箱", ("纸箱", "纸盒", "纸板")),
    ("木质家具", ("木", "家具", "柜")),
    ("泡沫", ("泡沫",)),
    ("布料", ("布", "衣物", "棉")),
    ("液化气罐", ("气罐", "燃气", "液化")),
    ("明火", ("明火", "火焰", "焚烧")),
)

VALID_LEVELS = ("none", "low", "medium", "high", "not_corridor")
VALID_SEVERITY = ("low", "medium", "high")


def _as_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "是", "1")
    return bool(value)


def normalize_level(value) -> str:
    """把模型给的等级收敛到白名单，非法值按低风险处理。"""
    level = str(value or "").strip().lower()
    return level if level in VALID_LEVELS else "low"


def normalize_hazard_type(value) -> str:
    """把模型给的元素名称归一到白名单，无法归类统一为"其他"。"""
    text = str(value or "").strip()
    if text in HAZARD_TYPES:
        return text
    for name, keywords in TYPE_KEYWORDS:
        if any(keyword in text for keyword in keywords):
            return name
    return "其他"


def normalize_severity(value) -> str:
    severity = str(value or "").strip().lower()
    return severity if severity in VALID_SEVERITY else "low"


def normalize_flammable(value) -> str:
    """是否可燃：是 / 否 / 不确定。"""
    text = str(value or "").strip()
    if text in ("是", "否", "不确定"):
        return text
    lowered = text.lower()
    if lowered in ("true", "yes") or "可燃" in text and "不可燃" not in text:
        return "是"
    if lowered == "false" or "不可燃" in text:
        return "否"
    return "不确定"


def normalize_hazards(raw: list) -> List[Hazard]:
    """把模型返回的隐患数组整理成统一结构，丢弃无效条目。"""
    result: List[Hazard] = []
    if not isinstance(raw, list):
        return result
    for item in raw:
        if not isinstance(item, dict):
            continue
        if not str(item.get("type") or "").strip():
            continue
        hazard = Hazard(
            type=normalize_hazard_type(item.get("type")),
            position=str(item.get("position") or "")[:60],
            flammable=normalize_flammable(item.get("flammable")),
            blocks_passage=_as_bool(item.get("blocks_passage")),
            ignition_risk=str(item.get("ignition_risk") or "")[:120],
            severity=normalize_severity(item.get("severity")),
        )
        result.append(hazard)
    return result


def _is_high_risk_item(hazard: Hazard) -> bool:
    text = f"{hazard.type} {hazard.ignition_risk}"
    return any(keyword in text for keyword in HIGH_KEYWORDS)


def review(model_level: str, is_corridor: bool, hazards: List[Hazard]) -> Tuple[str, bool]:
    """根据规则校正模型给出的等级。

    返回 (最终等级, 是否发生了校正)。
    """
    level = model_level if model_level in LEVEL_ORDER else "low"

    if not is_corridor:
        return "not_corridor", level != "not_corridor"

    if any(_is_high_risk_item(h) for h in hazards):
        return "high", level != "high"

    flammable = [h for h in hazards if h.flammable == "是"]
    blocking = [h for h in hazards if h.blocks_passage]
    # 大量可燃堆积且堵塞通行 -> 至少中等风险
    if blocking and flammable:
        if LEVEL_ORDER[level] < LEVEL_ORDER["medium"]:
            return "medium", True

    return level, False
