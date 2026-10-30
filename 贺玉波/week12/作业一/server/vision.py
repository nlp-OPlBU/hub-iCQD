# -*- coding: utf-8 -*-
"""DeepSeek 视觉模型调用封装。

接口依据 https://api-docs.deepseek.com/zh-cn/guides/vision ：
- base_url = https://api.deepseek.com
- model    = deepseek-flash（支持 image_url 输入）
- 图片以 base64 data URL 放在 user 消息的 content 块里
"""

import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import APIError, OpenAI
from PIL import Image

from .prompt import build_messages

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

_raw_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
# 占位符视为未配置，避免用无效 key 去调接口
API_KEY = "" if _raw_key in ("", "sk-", "sk-xxx") else _raw_key
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-flash").strip()
IMAGE_DETAIL = os.getenv("IMAGE_DETAIL", "original").strip()
# 复杂场景下 hazards 数组较长，900 容易被截断，默认给到 1600
MAX_TOKENS = int(os.getenv("DEEPSEEK_MAX_TOKENS", "1600"))

# 官方会把图片缩放到总像素约等于 1300x1300，这里按同样尺度预压缩，
# 既减少上传体积，也不会损失模型实际使用的细节。
MAX_SIDE = 1300
JPEG_QUALITY = 85

SUPPORTED_FORMATS = {"JPEG", "PNG", "GIF", "WEBP"}

_client: Optional[OpenAI] = None
logger = logging.getLogger("corridor.vision")


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not API_KEY:
            raise RuntimeError("未配置 DEEPSEEK_API_KEY，请在 .env 中填写")
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL, timeout=45, max_retries=1)
    return _client


def prepare_image(image_bytes: bytes) -> bytes:
    """校验图片并按长边 1300px、JPEG 0.85 重新编码。"""
    try:
        image = Image.open(io.BytesIO(image_bytes))
    except Exception as exc:  # noqa: BLE001
        raise ValueError("无法解析该图片，请上传 JPG / PNG / WEBP / GIF 格式的照片") from exc

    if image.format not in SUPPORTED_FORMATS:
        raise ValueError(f"不支持的图片格式：{image.format}")

    # GIF 取第一帧；带透明通道的统一铺白底再转 JPEG
    if getattr(image, "is_animated", False):
        image.seek(0)
    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGBA")
        background = Image.new("RGB", image.size, (255, 255, 255))
        background.paste(image, mask=image.split()[-1])
        image = background
    else:
        image = image.convert("RGB")

    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return buffer.getvalue()


def _request(data_url: str, max_tokens: Optional[int] = None):
    """调用 chat/completions，JSON 模式不被支持时自动退回普通模式。"""
    client = get_client()
    payload = dict(
        model=MODEL,
        messages=build_messages(data_url, IMAGE_DETAIL),
        temperature=0.1,
        max_tokens=max_tokens or MAX_TOKENS,
    )
    try:
        return client.chat.completions.create(
            response_format={"type": "json_object"}, **payload
        )
    except APIError:
        # 兼容个别模型/网关不支持 response_format 的情况
        return client.chat.completions.create(**payload)


def _extract_json(text: str) -> dict:
    """从模型输出中提取 JSON：兼容 ```json 代码块和前后夹带文字的情况。"""
    text = (text or "").strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
    return json.loads(text, strict=False)


def _call_and_parse(data_url: str, max_tokens: int):
    """发送请求并解析，返回 (response, finish_reason, content, parsed, parse_error)。"""
    response = _request(data_url, max_tokens)
    choice = response.choices[0]
    content = choice.message.content or ""
    try:
        parsed = _extract_json(content)
        parse_error = False
    except (json.JSONDecodeError, ValueError):
        parsed = None
        parse_error = True
    return response, choice.finish_reason, content, parsed, parse_error


def analyze_prepared(jpeg: bytes) -> dict:
    """对已预处理（长边 1300px 的 JPEG）的图片做隐患识别。"""
    b64 = base64.b64encode(jpeg).decode("utf-8")
    data_url = f"data:image/jpeg;base64,{b64}"

    started = time.perf_counter()
    response, finish, content, parsed, parse_error = _call_and_parse(data_url, MAX_TOKENS)

    # 截断是最常见的解析失败原因：模型输出超过 max_tokens，JSON 尾部被切断。
    # 这里加大预算重试一次，仍然失败才向上抛出。
    if parse_error:
        logger.warning(
            "首次解析失败 finish_reason=%s content_len=%s，提升 max_tokens 重试",
            finish,
            len(content),
        )
        retry_tokens = min(MAX_TOKENS * 2, 4000)
        response, finish, content, parsed, parse_error = _call_and_parse(data_url, retry_tokens)

    latency_ms = int((time.perf_counter() - started) * 1000)

    if parse_error:
        logger.warning("重试后仍未解析出 JSON，原始内容前 300 字：%s", content[:300].replace("\n", " "))

    usage = getattr(response, "usage", None)
    return {
        "data": parsed or {},
        "raw_text": content,
        "parse_error": parse_error,
        "finish_reason": finish,
        "model": getattr(response, "model", MODEL) or MODEL,
        "latency_ms": latency_ms,
        "tokens": getattr(usage, "total_tokens", None) if usage else None,
    }


def analyze_image(image_bytes: bytes) -> dict:
    """对一张图片做隐患识别（内部完成校验 + 压缩）。"""
    return analyze_prepared(prepare_image(image_bytes))
