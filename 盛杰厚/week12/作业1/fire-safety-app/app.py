"""
楼道消防安全风险识别 —— 前后端多模态应用（后端）

功能：
  - 提供前端页面（static/index.html）
  - 接收前端上传的图片（multipart/form-data）
  - 调用多模态大模型（默认 deepseek-flash，OpenAI 兼容接口）识别消防隐患
  - 返回风险等级 + 判断依据

运行：
  conda activate py312
  uvicorn app:app --host 0.0.0.0 --port 8000

配置（环境变量 或 同目录 .env 文件，二者取环境变量优先）：
  API_KEY        模型服务 API Key（必填，除非 MOCK=1）
  API_BASE_URL   模型服务地址，默认 https://openkey.cloud/v1
  MODEL_NAME     模型名称，默认 deepseek-flash（可选 deepseek-v4-pro）
  MOCK           设为 1 时使用本地模拟结果，便于没有 Key 时调试前后端
  TIMEOUT        模型调用超时秒数，默认 60
"""
import os
import re
import json
import base64
from pathlib import Path

import requests
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_dotenv(path: Path) -> None:
    """极简 .env 解析，避免引入额外依赖。已存在的环境变量优先级更高。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")

API_KEY = os.environ.get("API_KEY", "").strip()
API_BASE_URL = os.environ.get("API_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
MODEL_NAME = os.environ.get("MODEL_NAME", "deepseek-flash").strip()
MOCK = os.environ.get("MOCK", "0").strip() in ("1", "true", "True", "yes")
TIMEOUT = int(os.environ.get("TIMEOUT", "60"))
MAX_IMAGE_BYTES = int(os.environ.get("MAX_IMAGE_BYTES", str(12 * 1024 * 1024)))


# ---------------------------------------------------------------------------
# 风险类别定义（与前端保持同一套中文标签）
# ---------------------------------------------------------------------------
RISK_LEVELS = {
    "高风险": {"id": "high", "color": "#d32f2f", "desc": "存在电动车/电瓶/飞线充电等可能起火的元素"},
    "中风险": {"id": "medium", "color": "#f57c00", "desc": "大量堆积物严重影响通行，或纸箱/木质家具等可燃堵塞物"},
    "低风险": {"id": "low", "color": "#fbc02d", "desc": "存在轻微堆物现象，暂不严重"},
    "无风险": {"id": "none", "color": "#388e3c", "desc": "楼道干净，无堆放物品"},
    "非楼道": {"id": "not_corridor", "color": "#78909c", "desc": "图片内容与楼道无关"},
}

CANONICAL = list(RISK_LEVELS.keys())

# 模型若未输出标准类别时的别名兜底（按优先级匹配）
RISK_ALIASES = [
    ("高风险", "高风险"), ("中等风险", "中风险"), ("中风险", "中风险"),
    ("低风险", "低风险"), ("无风险", "无风险"), ("无隐患", "无风险"),
    ("非楼道", "非楼道"),
    ("电动", "高风险"), ("电瓶", "高风险"), ("飞线", "高风险"), ("起火", "高风险"),
    ("大量", "中风险"), ("堵塞", "中风险"), ("纸箱", "中风险"), ("木质", "中风险"),
    ("干净", "无风险"), ("正常", "无风险"), ("空旷", "无风险"),
    ("无关", "非楼道"),
]


SYSTEM_PROMPT = (
    "你是一名专业的消防安全检查员，擅长识别楼道内的消防安全隐患。"
    "请仔细分析用户提供的图片，只依据图片内容做出判断。"
)

USER_PROMPT = """请识别图片中的场景是否存在消防安全隐患以及隐患的危险程度，并输出严格符合格式的 JSON。

分类标准：
- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：图片内容与楼道无关。

请严格只输出如下 JSON，不要输出任何其他内容、不要加代码块标记：
{"risk": "高风险|中风险|低风险|无风险|非楼道", "reason": "一句话判断依据"}"""


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def guess_mime(content_type: str | None, filename: str | None, data: bytes) -> str:
    """根据 Content-Type、文件头、扩展名推断图片 MIME 类型。"""
    if content_type and content_type.startswith("image/"):
        return content_type
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    ext = os.path.splitext(filename or "")[1].lower()
    return {
        ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
    }.get(ext, "image/jpeg")


def normalize_risk(risk_text: str) -> str | None:
    """把模型输出的 risk 字段规整到标准类别，无法识别时返回 None。"""
    risk_text = (risk_text or "").strip()
    if not risk_text:
        return None
    for key in CANONICAL:
        if key in risk_text:
            return key
    for alias, canonical in RISK_ALIASES:
        if alias in risk_text:
            return canonical
    return None


def normalize(risk: str, reason: str, raw: str) -> dict:
    canonical = normalize_risk(risk)
    if canonical is None:
        # 从原始文本再兜底一次
        canonical = normalize_risk(raw) or "非楼道"
        if not reason:
            # 模型未按要求输出 JSON，直接把原文作为判断依据（截断防止过长）
            reason = raw.strip()[:500] or RISK_LEVELS[canonical]["desc"]
    return {
        "risk": canonical,
        "reason": reason or RISK_LEVELS[canonical]["desc"],
        "raw": raw,
    }


def parse_result(text: str) -> dict:
    text = (text or "").strip()
    cleaned = re.sub(r"```(?:json)?", "", text).strip()

    # 1) 直接解析整个文本为 JSON
    for candidate in (cleaned,):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return normalize(str(obj.get("risk", "")), str(obj.get("reason", "")), text)
        except (json.JSONDecodeError, AttributeError):
            pass

    # 2) 从文本中抠出第一个 {...}
    m = re.search(r"\{.*\}", cleaned, re.S)
    if m:
        try:
            obj = json.loads(m.group(0))
            if isinstance(obj, dict):
                return normalize(str(obj.get("risk", "")), str(obj.get("reason", "")), text)
        except json.JSONDecodeError:
            pass

    # 3) 兜底：关键词匹配
    return normalize("", "", text)


def call_model(image_b64: str, mime: str) -> dict:
    """调用 OpenAI 兼容的多模态接口。"""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image_b64}"}},
                {"type": "text", "text": USER_PROMPT},
            ],
        },
    ]

    url = f"{API_BASE_URL}/chat/completions"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"}
    payload = {
        "model": MODEL_NAME,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": 512,
        "stream": False,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"请求模型接口失败：{exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(f"模型接口返回 {resp.status_code}：{resp.text[:500]}")

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(
            f"模型接口响应格式异常：{resp.text[:500]}"
        ) from exc

    # 部分接口 content 可能为数组
    if isinstance(content, list):
        content = "".join(
            p.get("text", "") if isinstance(p, dict) else str(p) for p in content
        )

    return parse_result(str(content))


def mock_analyze(image_b64: str, mime: str) -> dict:
    """本地模拟结果，用于无 API Key 时调试前后端流程。"""
    del image_b64, mime
    return {
        "risk": "低风险",
        "reason": "（模拟结果）检测到楼道存在轻微堆物现象，未发现电动车、飞线充电或大面积可燃物。",
        "raw": "mock",
    }


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="楼道消防安全风险识别", version="1.0.0")

# 挂载前端静态目录
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "api_base_url": API_BASE_URL,
        "mock": MOCK,
        "api_key_configured": bool(API_KEY),
    }


@app.post("/api/analyze")
async def analyze(file: UploadFile = File(...)) -> dict:
    if not API_KEY and not MOCK:
        raise HTTPException(
            status_code=500,
            detail="未配置 API Key。请设置环境变量 API_KEY，或在 .env 文件中填写，"
                   "或设置 MOCK=1 进行本地模拟调试。",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="上传的图片为空")
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"图片过大（超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB）",
        )

    mime = guess_mime(file.content_type, file.filename, data)
    image_b64 = base64.b64encode(data).decode("ascii")

    try:
        if MOCK:
            result = mock_analyze(image_b64, mime)
        else:
            result = call_model(image_b64, mime)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    # 附带标准类别的元信息，方便前端展示
    result["level"] = RISK_LEVELS[result["risk"]]
    return result


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
