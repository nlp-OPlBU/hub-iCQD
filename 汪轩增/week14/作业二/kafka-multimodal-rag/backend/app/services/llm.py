"""多模态大模型调用（OpenAI 兼容接口，默认通义千问 Qwen-VL）。

输出里的图片占位符 [[IMG_n]] 会被替换成真实 markdown 图片链接，
从而实现“图文混排”的答案。
"""

import base64
import logging
import mimetypes
import re
from pathlib import Path

import openai

from ..config import Settings, get_settings
from .media import ImageRef, UserImage

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个严谨的多模态知识库问答助手。

规则：
1. 只能依据提供的“检索资料”和用户上传的图片回答，不得编造资料中不存在的事实。
2. 资料中的图片用 [[IMG_n]] 占位符表示。答案中需要展示某张图片时，请原样输出该占位符（例如 [[IMG_2]]），系统会替换为真实图片链接。不要编造图片地址，也不要使用资料中不存在的编号。
3. 尽量把图片放在与之相关的文字附近，实现图文混排。
4. 引用信息时在句末标注来源，例如 [资料1]。
5. 使用简体中文，答案简洁、有条理，可以使用 Markdown。
6. 如果资料不足以回答，请直接说明，不要臆测。
"""

USER_PROMPT_TEMPLATE = """用户问题：
{question}

检索到的资料：
{context}

请给出回答。若资料中有与答案相关的图片，请把对应的 [[IMG_n]] 占位符插入到合适位置。"""

PLACEHOLDER_RE = re.compile(r"\[{1,2}\s*IMG[_\-\s]?(\d+)\s*\]{1,2}", re.IGNORECASE)


class LLMNotConfigured(RuntimeError):
    pass


class LLMService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._client: openai.OpenAI | None = None

    def _get_client(self) -> openai.OpenAI:
        if not self.settings.llm_api_key:
            raise LLMNotConfigured("未配置 LLM_API_KEY，请在 backend/.env 中填写")
        if self._client is None:
            self._client = openai.OpenAI(
                api_key=self.settings.llm_api_key,
                base_url=self.settings.llm_base_url,
                timeout=120,
            )
        return self._client

    def answer(
        self,
        question: str,
        context: str,
        image_refs: list[ImageRef],
        user_images: list[UserImage],
    ) -> str:
        content: list[dict] = [
            {
                "type": "text",
                "text": USER_PROMPT_TEMPLATE.format(question=question, context=context),
            }
        ]

        # 检索到的文档图片：作为视觉输入，让模型真正“看懂”图表
        used_images = 0
        for ref in image_refs:
            if used_images >= self.settings.max_images_to_llm:
                break
            local_path = self.settings.static_url_to_local(ref.static_url)
            if local_path is None:
                continue
            data_url = _file_to_data_url(
                local_path, self.settings.max_image_bytes_mb * 1024 * 1024
            )
            if data_url is None:
                continue
            content.append(
                {
                    "type": "text",
                    "text": f"资料图片 [[IMG_{ref.index}]]（来自：{ref.source_file}）",
                }
            )
            content.append({"type": "image_url", "image_url": {"url": data_url}})
            used_images += 1

        # 用户随问题上传的图片
        if user_images:
            content.append(
                {
                    "type": "text",
                    "text": f"用户随问题上传了 {len(user_images)} 张图片，请结合图片内容回答。",
                }
            )
            for i, image in enumerate(user_images, start=1):
                content.append({"type": "text", "text": f"用户图片 {i}："})
                content.append(
                    {"type": "image_url", "image_url": {"url": image.data_url}}
                )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        response = self._get_client().chat.completions.create(
            model=self.settings.llm_model,
            messages=messages,
            temperature=self.settings.llm_temperature,
        )

        message = response.choices[0].message
        result = message.content
        if isinstance(result, list):
            result = "".join(
                part.get("text", "")
                for part in result
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return (result or "").strip()


def render_answer(answer: str, image_refs: list[ImageRef]) -> str:
    """把模型输出里的 [[IMG_n]] 换成真实 markdown 图片链接。"""
    mapping = {ref.index: ref.absolute_url for ref in image_refs}

    def _replace(match: re.Match) -> str:
        index = int(match.group(1))
        url = mapping.get(index)
        return f"![图{index}]({url})" if url else ""

    return PLACEHOLDER_RE.sub(_replace, answer)


def _file_to_data_url(path: Path, max_bytes: int | None = None) -> str | None:
    try:
        if max_bytes is not None and path.stat().st_size > max_bytes:
            logger.warning("图片过大，跳过视觉输入: %s", path)
            return None
        data = path.read_bytes()
    except OSError:
        return None
    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
