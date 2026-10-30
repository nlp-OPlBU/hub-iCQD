"""调用 OpenAI 兼容的多模态大模型，完成楼道消防隐患识别与结构化解析。"""

from __future__ import annotations

import base64
import io
import json
import re
import time
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    AuthenticationError,
    OpenAI,
    RateLimitError,
)
from PIL import Image, ImageOps

from config import settings

LEVELS = ["高风险", "中风险", "低风险", "无风险", "非楼道"]
LEVEL_CODE = {"高风险": "high", "中风险": "medium", "低风险": "low", "无风险": "none", "非楼道": "other"}
# 用于把等级折算成 0-100 的“危险度”参考值
LEVEL_BASE = {"高风险": 92, "中风险": 64, "低风险": 34, "无风险": 6, "非楼道": 0}

SYSTEM_PROMPT = """你是一名专业消防安全隐患排查专家，专门通过现场照片检查居民楼楼道（含楼梯间、疏散通道）的火灾隐患。
你的判断必须严格依据照片中真实可见的内容，不得臆测、不得编造照片里不存在的物品。
若照片与楼道无关（如室内房间、街景、餐桌、风景、人物特写等），必须判定为“非楼道”。"""

USER_PROMPT = """请识别这张照片中是否存在楼道消防安全隐患，并判断隐患的危险程度。

分级标准：
- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：与楼道无关的图片。

只输出一个 JSON 对象，不要输出 Markdown 代码块或额外说明，格式如下：
{
  "is_stairwell": true,
  "risk_level": "高风险",
  "confidence": 0.85,
  "hazards": [
    {"type": "电动车", "location": "一层楼梯口", "detail": "电动车停放在楼道内，靠近疏散通道"}
  ],
  "evidence": ["照片中可见一辆电动车停放在楼梯转角处", "车辆旁有充电线"],
  "advice": "立即将电动车移出楼道，禁止在楼道内充电"
}

字段要求：
- is_stairwell：照片是否拍摄楼道，布尔值。
- risk_level：只能是“高风险 / 中风险 / 低风险 / 无风险 / 非楼道”之一。
- confidence：0 到 1 之间的置信度。
- hazards：隐患元素数组，无隐患时为空数组 []；type 用简短名词（如电动车、电瓶、飞线充电、纸箱、木质家具、杂物堆积、其他）。
- evidence：判断依据，逐条描述照片中看到的客观事实，2-4 条。
- advice：给物业或住户的处置建议，一到两句。"""

_LEVEL_PATTERNS = [
    ("非楼道", ("非楼道", "不是楼道", "与楼道无关", "无关图片")),
    ("高风险", ("高风险", "高危", "风险很高", "high")),
    ("中风险", ("中风险", "中等风险", "中度风险", "medium")),
    ("低风险", ("低风险", "较低风险", "low")),
    ("无风险", ("无风险", "无隐患", "没有隐患", "干净", "安全", "none")),
]


class AnalysisError(Exception):
    """对外暴露的可读错误。"""

    def __init__(self, code: str, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def prepare_image(raw: bytes) -> tuple[str, int, int]:
    """校正方向、限制尺寸后转成 JPEG data URL，减少上传体积与视觉 token。"""
    try:
        image = Image.open(io.BytesIO(raw))
        image = ImageOps.exif_transpose(image)
        if image.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", image.size, (255, 255, 255))
            background.paste(image.convert("RGBA"), mask=image.convert("RGBA").split()[-1])
            image = background
        else:
            image = image.convert("RGB")
    except Exception as exc:  # 只有在这里才需要兜底，图片来自浏览器上传
        raise AnalysisError("invalid_image", "无法解析这张图片，请换一张 JPG / PNG 照片重试。", 400) from exc

    image.thumbnail((settings.max_image_side, settings.max_image_side), Image.LANCZOS)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=85, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}", image.width, image.height


def _extract_json(text: str) -> dict[str, Any] | None:
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    candidates = [cleaned]
    if "{" in cleaned and "}" in cleaned:
        candidates.append(cleaned[cleaned.find("{"): cleaned.rfind("}") + 1])
    for candidate in candidates:
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize_level(value: Any, is_stairwell: bool | None) -> str:
    if is_stairwell is False:
        return "非楼道"
    text = str(value or "").strip().lower()
    for level, keywords in _LEVEL_PATTERNS:
        if any(keyword in text for keyword in keywords):
            return level
    return ""


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        item = item.strip()
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        return [part for part in re.split(r"[；;\n]", value) if part.strip()]
    return [value]


def _normalize_hazards(value: Any) -> list[dict[str, str]]:
    hazards: list[dict[str, str]] = []
    for item in _as_list(value):
        if isinstance(item, dict):
            hazard_type = str(item.get("type") or item.get("name") or item.get("隐患") or "隐患").strip()
            hazards.append(
                {
                    "type": hazard_type,
                    "location": str(item.get("location") or item.get("位置") or "").strip(),
                    "detail": str(item.get("detail") or item.get("description") or item.get("说明") or "").strip(),
                }
            )
        elif str(item).strip():
            hazards.append({"type": str(item).strip(), "location": "", "detail": ""})
    return hazards


def _fallback_from_text(text: str) -> dict[str, Any]:
    """模型没按 JSON 返回时，用关键词兜底，保证页面仍可展示结果。"""
    level = _normalize_level(text, None)
    if not level:
        raise AnalysisError("bad_output", "模型返回内容无法解析为风险等级，请重试或更换模型。")
    return {"risk_level": level, "is_stairwell": level != "非楼道", "hazards": [], "evidence": [text.strip()[:300]]}


def normalize_result(data: dict[str, Any]) -> dict[str, Any]:
    is_stairwell_raw = data.get("is_stairwell")
    is_stairwell = None if is_stairwell_raw is None else bool(is_stairwell_raw)
    level = _normalize_level(
        data.get("risk_level") or data.get("risk") or data.get("level") or data.get("风险等级") or data.get("风险类别"),
        is_stairwell,
    )
    if not level:
        level = "非楼道" if is_stairwell is False else ""

    try:
        confidence = float(data.get("confidence", data.get("置信度", 0.7)))
        confidence = confidence / 100 if confidence > 1 else confidence
    except (TypeError, ValueError):
        confidence = 0.7
    confidence = min(max(confidence, 0.0), 1.0)

    hazards = _normalize_hazards(data.get("hazards") or data.get("隐患") or data.get("risks"))
    evidence = [str(item).strip() for item in _as_list(data.get("evidence") or data.get("依据") or data.get("reasons"))]
    advice = str(data.get("advice") or data.get("suggestion") or data.get("建议") or "").strip()

    base = LEVEL_BASE.get(level, 0)
    return {
        "is_stairwell": level != "非楼道",
        "risk_level": level or "待确认",
        "risk_code": LEVEL_CODE.get(level, "other"),
        "confidence": round(confidence, 2),
        # 危险度：以等级为基准，结合模型置信度做小幅修正
        "score": round(base * (0.7 + 0.3 * confidence)),
        "hazards": hazards,
        "evidence": _dedupe(evidence),
        "advice": advice,
    }


def _build_client() -> OpenAI:
    if not settings.api_key:
        raise AnalysisError(
            "not_configured",
            "尚未配置 API Key，请在 stairwell_hazards/.env 中填写 OPENAI_BASE_URL 和 OPENAI_API_KEY 后重启服务。",
            503,
        )
    return OpenAI(
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout=settings.timeout,
        max_retries=0,
        default_headers={"User-Agent": settings.user_agent} if settings.user_agent else None,
    )


def analyze(image_raw: bytes, model: str) -> dict[str, Any]:
    data_url, width, height = prepare_image(image_raw)
    client = _build_client()

    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": USER_PROMPT},
                ],
            },
        ],
        "temperature": 0.1,
        "max_tokens": 900,
    }
    if settings.json_mode:
        payload["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    try:
        response = client.chat.completions.create(**payload)
    except AuthenticationError as exc:
        raise AnalysisError("auth_failed", "API Key 无效或已过期，请检查 .env 配置。", 401) from exc
    except RateLimitError as exc:
        raise AnalysisError("rate_limited", "调用频率或额度受限，请稍后重试。", 429) from exc
    except APIConnectionError as exc:
        raise AnalysisError("network_error", "无法连接模型网关，请检查网络或 BASE_URL。", 502) from exc
    except APIStatusError as exc:
        detail = getattr(exc, "message", None) or str(exc)
        raise AnalysisError("upstream_error", f"模型服务返回错误（HTTP {exc.status_code}）：{detail[:200]}", 502) from exc
    elapsed_ms = int((time.perf_counter() - started) * 1000)

    message = response.choices[0].message.content if response.choices else ""
    text = message if isinstance(message, str) else "".join(part.text for part in message or [] if part.text)
    parsed = _extract_json(text) if text else None
    if parsed is None:
        if not text:
            raise AnalysisError("empty_output", "模型没有返回任何内容，请重试。")
        parsed = _fallback_from_text(text)

    result = normalize_result(parsed)
    result.update(
        {
            "model": model,
            "elapsed_ms": elapsed_ms,
            "image_size": {"width": width, "height": height},
            "raw": text.strip(),
        }
    )
    return result
