"""Parse and normalise the emotion JSON returned by the LLM."""

import json
import re
from typing import Any

from .schemas import EmotionLabel, EmotionResult

_LABEL_ALIASES: dict[str, EmotionLabel] = {
    "positive": "positive",
    "pos": "positive",
    "积极": "positive",
    "正向": "positive",
    "negative": "negative",
    "neg": "negative",
    "消极": "negative",
    "负面": "negative",
    "neutral": "neutral",
    "中性": "neutral",
    "中立": "neutral",
}


class EmotionParseError(ValueError):
    """Raised when the model response cannot be interpreted as an emotion result."""


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _extract_json_object(text: str) -> dict[str, Any]:
    candidate = _strip_code_fence(text)
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start == -1 or end <= start:
            raise EmotionParseError("模型未返回 JSON 对象") from None
        try:
            payload = json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise EmotionParseError("模型返回的 JSON 无法解析") from exc

    if not isinstance(payload, dict):
        raise EmotionParseError("模型返回的 JSON 不是对象")
    return payload


def _normalise_label(value: Any) -> EmotionLabel:
    if not isinstance(value, str):
        raise EmotionParseError("情感标签缺失或类型错误")
    label = _LABEL_ALIASES.get(value.strip().lower())
    if label is None:
        raise EmotionParseError(f"未知的情感标签：{value!r}")
    return label


def _normalise_confidence(value: Any) -> float:
    if value is None:
        return 0.5
    try:
        confidence = float(str(value).strip().rstrip("%"))
    except (TypeError, ValueError):
        return 0.5
    if "%" in str(value) or confidence > 1.0:
        confidence /= 100.0
    return round(min(max(confidence, 0.0), 1.0), 4)


def parse_emotion_json(raw: str) -> EmotionResult:
    payload = _extract_json_object(raw)
    label = _normalise_label(payload.get("label") or payload.get("emotion"))
    reason_value = payload.get("reason") or payload.get("explanation") or ""
    reason = str(reason_value).strip()[:200]
    confidence = _normalise_confidence(
        payload.get("confidence", payload.get("score"))
    )
    return EmotionResult(label=label, confidence=confidence, reason=reason)
