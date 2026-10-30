import base64
import json
import os

import requests

# ---------------------------------------------------------------------------
# 风险识别提示词（与《案例-多模态内容理解.ipynb》保持一致的五级分类）
# ---------------------------------------------------------------------------
RISK_PROMPT = """你是一名专业的消防安全专家，擅长识别各种风险。请识别图片中的场景是否存在消防安全隐患，并判断危险程度。

请根据楼道中是否存在大量堆积物、堆积物是否可燃以及是否有起火风险，将隐患分为以下五类之一：

- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：一些与楼道无关的图片。

请只输出一个 JSON 对象，不要输出任何其他文字、解释或代码块，格式如下：
{"risk_level": "高风险", "reason": "一句话说明判断依据"}
其中 risk_level 只能是上面五个类别之一。"""

# ---------------------------------------------------------------------------
# 服务商配置：base_url / api_key / model 均可通过环境变量覆盖
# ---------------------------------------------------------------------------
PROVIDERS = {
    "deepseek": {
        "base_url": os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "api_key": os.environ.get("DEEPSEEK_API_KEY", "sk-578447a1546e7bf29d9ab5e8edcf2"),
        # DeepSeek Flash 多模态模型（旧名 deepseek-v4-flash-vision-exp 仍可调用）
        "model": os.environ.get("DEEPSEEK_MODEL", "deepseek-flash"),
    },
    "qwen": {
        "base_url": os.environ.get(
            "DASHSCOPE_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        ),
        "api_key": os.environ.get("DASHSCOPE_API_KEY", ""),
        "model": os.environ.get("QWEN_MODEL", "qwen-vl-plus"),
    },
}

# 模型输出的风险等级 -> 规范化后的标准类别
_RISK_ALIASES = {
    "高风险": "高风险",
    "中风险": "中风险",
    "中等风险": "中风险",
    "低风险": "低风险",
    "无风险": "无风险",
    "无隐患": "无风险",
    "非楼道": "非楼道",
}


def _to_data_url(image_bytes: bytes, mime: str = "image/jpeg") -> str:
    b64 = base64.b64encode(image_bytes).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _parse_result(content: str) -> dict:
    """把模型返回的文本解析成 {"risk_level": ..., "reason": ...}。

    模型可能直接返回 JSON，也可能套了 markdown 代码块或在 JSON 前后夹带文字，
    这里都尽量容错处理。
    """
    text = (content or "").strip()

    # 去掉 markdown 代码块围栏
    if text.startswith("```"):
        text = text.strip("`").strip()
        if text.lower().startswith("json"):
            text = text[4:].strip()

    obj = None
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        # 尝试截取第一个 { 到最后一个 } 之间的内容
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
            except (json.JSONDecodeError, ValueError):
                obj = None

    raw_level = ""
    reason = ""
    if isinstance(obj, dict):
        raw_level = str(obj.get("risk_level", "")).strip()
        reason = str(obj.get("reason", "")).strip()

    level = _RISK_ALIASES.get(raw_level)
    if level is None:
        # 容错：在原文里找是否有已知等级关键词（优先级：高 > 中 > 低 > 无 > 非楼道）
        level = "非楼道"
        for alias in ("高风险", "中风险", "中等风险", "低风险", "无风险", "无隐患", "非楼道"):
            if alias in raw_level or alias in text:
                level = _RISK_ALIASES[alias]
                break

    if not reason:
        reason = text if not isinstance(obj, dict) else ""

    return {"risk_level": level, "reason": reason}


def analyze_risk(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    provider: str | None = None,
) -> dict:
    """识别一张图片的楼道消防安全风险。

    返回 {"risk_level": 五类之一, "reason": 说明, "provider": 服务商, "model": 模型名}
    """
    provider = provider or os.environ.get("VISION_PROVIDER", "deepseek")
    if provider not in PROVIDERS:
        raise RuntimeError(f"未知的服务商: {provider}，可选 {list(PROVIDERS)}")

    cfg = PROVIDERS[provider]
    if not cfg["api_key"]:
        raise RuntimeError(
            f"缺少 {provider} 的 API Key，请设置环境变量 "
            f"（deepseek -> DEEPSEEK_API_KEY，qwen -> DASHSCOPE_API_KEY）"
        )

    url = cfg["base_url"].rstrip("/") + "/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {cfg['api_key']}",
    }
    payload = {
        "model": cfg["model"],
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": RISK_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": _to_data_url(image_bytes, mime)},
                    },
                ],
            }
        ],
        "temperature": 0.1,
    }

    resp = requests.post(url, headers=headers, json=payload, timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(
            f"调用 {provider} 失败：HTTP {resp.status_code} - {resp.text[:500]}"
        )

    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    result = _parse_result(content)
    result["provider"] = provider
    result["model"] = cfg["model"]
    return result
