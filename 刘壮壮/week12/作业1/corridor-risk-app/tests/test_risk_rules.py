import pytest

from backend.risk_rules import classify_risk
from backend.schemas import RiskLevel, SceneEvidence


def scene(**overrides) -> SceneEvidence:
    values = {
        "is_corridor": True,
        "corridor_confidence": 0.95,
        "high_risk_elements": [],
        "clutter_level": "none",
        "combustible_load": "none",
        "passage_blocked": False,
        "observations": [],
        "scene_summary": "测试场景",
        "confidence": 0.9,
    }
    values.update(overrides)
    return SceneEvidence(**values)


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        (scene(is_corridor=False), RiskLevel.NOT_CORRIDOR),
        (scene(), RiskLevel.NONE),
        (scene(clutter_level="light"), RiskLevel.LOW),
        (scene(combustible_load="heavy"), RiskLevel.MEDIUM),
        (
            scene(high_risk_elements=["电动车"], clutter_level="heavy"),
            RiskLevel.HIGH,
        ),
    ],
)
def test_all_risk_levels(evidence: SceneEvidence, expected: RiskLevel) -> None:
    assert classify_risk(evidence) == expected


def test_high_risk_takes_priority_over_blockage() -> None:
    evidence = scene(
        high_risk_elements=["飞线充电"],
        passage_blocked=True,
        clutter_level="heavy",
    )
    assert classify_risk(evidence) == RiskLevel.HIGH


def test_non_corridor_takes_priority_over_visible_objects() -> None:
    evidence = scene(is_corridor=False, high_risk_elements=["电瓶"])
    assert classify_risk(evidence) == RiskLevel.NOT_CORRIDOR
