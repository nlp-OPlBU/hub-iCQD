import base64
import io
import json
import os

from dotenv import load_dotenv
from openai import OpenAI
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import ValidationError

from backend.schemas import SceneEvidence

load_dotenv()

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_IMAGE_SIDE = 1600

SYSTEM_PROMPT = """
你是专业的消防安全图像巡检员。请仅依据图片中清晰可见的内容提取事实，
不要臆测被遮挡区域，也不要因为普通自行车而判断为电动车。

判定口径：
1. high_risk_elements 只填写电动车、电瓶、飞线充电、充电器连接电动车/电瓶等直接起火风险；
2. clutter_level: none=无堆物，light=少量且基本不影响通行，heavy=大量或明显占用通道；
3. combustible_load: none=无可见可燃堆物，light=少量纸箱/木材/织物，heavy=大量可助燃蔓延；
4. passage_blocked 仅在疏散通道被明显压缩或堵塞时为 true；
5. 住宅楼道、楼梯间、门厅、地下通道可视为楼道；室外、卧室、厨房、停车场等为非楼道。

必须返回合法 json，不要输出 Markdown。严格使用以下结构：
{
  "is_corridor": true,
  "corridor_confidence": 0.95,
  "high_risk_elements": [],
  "clutter_level": "none",
  "combustible_load": "none",
  "passage_blocked": false,
  "observations": ["楼道地面清洁", "疏散通道畅通"],
  "scene_summary": "住宅楼道内未见明显堆物。",
  "confidence": 0.92
}
""".strip()


class InvalidImageError(ValueError):
    pass


class ModelServiceError(RuntimeError):
    pass


def prepare_image(image_bytes: bytes) -> str:
    """验证、纠正方向并缩放图片，返回 JPEG data URL。"""
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            if source.format not in SUPPORTED_FORMATS:
                raise InvalidImageError("仅支持 JPEG、PNG 或 WebP 图片")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGB")
    except InvalidImageError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise InvalidImageError("文件不是有效的图片或图片已损坏") from exc

    image.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE), Image.Resampling.LANCZOS)
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=85, optimize=True)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def _extract_scene(data_url: str) -> SceneEvidence:
    api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        raise ModelServiceError("服务端尚未配置 DEEPSEEK_API_KEY")

    client = OpenAI(
        api_key=api_key,
        base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        timeout=float(os.getenv("MODEL_TIMEOUT_SECONDS", "45")),
        max_retries=1,
    )

    try:
        response = client.chat.completions.create(
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "分析这张图片并按要求输出 json。"},
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url, "detail": "high"},
                        },
                    ],
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=700,
            temperature=0,
        )
        content = response.choices[0].message.content
        if not content:
            raise ModelServiceError("模型返回了空结果，请重试")
        return SceneEvidence.model_validate(json.loads(content))
    except ModelServiceError:
        raise
    except (json.JSONDecodeError, ValidationError, IndexError, AttributeError) as exc:
        raise ModelServiceError("模型结果格式异常，请重试") from exc
    except Exception as exc:
        # 不把供应商响应、请求体或密钥暴露给前端。
        raise ModelServiceError("模型服务暂时不可用，请稍后重试") from exc


def analyze_image(image_bytes: bytes) -> SceneEvidence:
    data_url = prepare_image(image_bytes)
    return _extract_scene(data_url)
