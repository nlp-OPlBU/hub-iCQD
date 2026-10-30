"""楼道消防隐患识别后端服务。

接收前端拍摄的照片，调用多模态大模型进行风险识别，
返回五级分类：高风险 / 中风险 / 低风险 / 无风险 / 非楼道。
支持 provider: dashscope（Qwen-Omni）。
"""
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

PROVIDER = os.getenv("MODEL_PROVIDER", "dashscope").lower()

PROVIDER_CONFIGS = {
    "dashscope": {
        "key_env": "DASHSCOPE_API_KEY",
        "base_url": os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": os.getenv("DASHSCOPE_MODEL", "qwen3-omni-flash"),
    },
}

cfg = PROVIDER_CONFIGS.get(PROVIDER, PROVIDER_CONFIGS["dashscope"])
KEY_ENV = cfg["key_env"]
API_KEY = os.getenv(KEY_ENV, "")
BASE_URL = cfg["base_url"]
MODEL = cfg["model"]

_client = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        if not API_KEY:
            raise HTTPException(
                status_code=500,
                detail=f"未配置 API Key，请在 backend/.env 中设置 {KEY_ENV}",
            )
        _client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
    return _client

CATEGORIES = ["高风险", "中风险", "低风险", "无风险", "非楼道"]

PROMPT = """你是一个专业消防专家，擅长识别各种风险。请识别照片中的消防安全隐患以及隐患的危险程度。
根据楼道中是否存在大量堆积物、堆积物是否可燃以及是否有起火风险，将隐患分为以下类别：

- 高风险：楼道中出现电动车、电瓶、飞线充电等可能起火的元素。
- 中风险：楼道中存在大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物。
- 低风险：存在楼道堆物现象但不严重。
- 无风险：楼道干净，无堆放物品。
- 非楼道：照片并非楼道场景（与楼道无关的图片）。

请严格按如下 JSON 格式输出，不要输出其他内容：
{"category": "上述五个类别之一", "reason": "一句话说明判断依据"}"""

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

app = FastAPI(title="楼道消防隐患识别")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    image: str  # data URL, e.g. "data:image/jpeg;base64,..."


def parse_result(text: str) -> dict:
    """从模型输出中解析分类结果，带容错。"""
    try:
        data = json.loads(text)
        category = str(data.get("category", "")).strip()
        reason = str(data.get("reason", "")).strip()
    except (json.JSONDecodeError, AttributeError):
        category, reason = "", text.strip()
        m = re.search(r'"?category"?\s*[:：]\s*"?([^",}\s]+)"?', text)
        if m:
            category = m.group(1).strip()
        m = re.search(r'"?reason"?\s*[:：]\s*"([^"]+)"', text)
        if m:
            reason = m.group(1).strip()

    matched = next((c for c in CATEGORIES if c in category or category in c), None)
    if not matched:
        matched = next((c for c in CATEGORIES if c in text), "非楼道")
    return {"category": matched, "reason": reason or text.strip(), "raw": text}


@app.post("/api/analyze")
async def analyze(req: AnalyzeRequest):
    if not req.image.startswith("data:image/"):
        raise HTTPException(status_code=400, detail="无效的图像数据")
    if len(req.image) > 20 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图像过大")

    client = get_client()
    is_omni = "omni" in MODEL.lower()
    try:
        if is_omni:
            # qwen-omni 系列在 OpenAI 兼容模式下仅支持流式调用，且需指定 modalities
            stream = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": req.image}},
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
                modalities=["text"],
                stream=True,
                stream_options={"include_usage": True},
            )
            chunks = []
            for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    chunks.append(chunk.choices[0].delta.content)
            text = "".join(chunks)
        else:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": req.image}},
                            {"type": "text", "text": PROMPT},
                        ],
                    }
                ],
                max_tokens=300,
                temperature=0.1,
            )
            text = resp.choices[0].message.content or ""
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"模型调用失败: {e}")

    result = parse_result(text)
    result["provider"] = PROVIDER
    result["model"] = MODEL
    return result


@app.get("/api/health")
async def health():
    return {"status": "ok", "provider": PROVIDER, "model": MODEL}


@app.get("/")
async def index():
    return FileResponse(FRONTEND_DIR / "index.html")


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
