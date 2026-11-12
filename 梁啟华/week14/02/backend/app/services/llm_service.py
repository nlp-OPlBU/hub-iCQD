"""
LLM 多模态服务 - 支持文本问答和图片理解

兼容 OpenAI 格式 API（通义千问、GPT-4o 等）
"""
import base64
import mimetypes
from pathlib import Path
from typing import Dict, List, Optional, Union

import httpx
from loguru import logger

from ..config_loader import Config


class LLMService:
    """LLM 调用服务（支持多模态 / Vision）"""

    def __init__(self):
        cfg = Config.get_section('llm')
        self.backend = cfg.get('backend', 'qwen-vl')
        self.text_model = cfg.get('text_model', 'qwen-plus')
        self.vision_model = cfg.get('vision_model', 'qwen-vl-plus')
        self.api_base = cfg.get('api_base', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        self.api_key = cfg.get('api_key', '')
        self.timeout = cfg.get('timeout', 60)
        self.temperature = cfg.get('temperature', 0.7)
        self.max_tokens = cfg.get('max_tokens', 2000)

        if not self.api_key or self.api_key.startswith('${'):
            logger.warning("LLM API Key 未配置！请设置环境变量 DASHSCOPE_API_KEY")

    def _get_url(self, endpoint: str) -> str:
        base = self.api_base.rstrip('/')
        return f"{base}/{endpoint}"

    def _encode_image(self, image_path: str) -> str:
        """将本地图片转为 data URL"""
        img_path = Path(image_path)
        if not img_path.exists():
            raise FileNotFoundError(f"图片不存在: {image_path}")

        mime_type, _ = mimetypes.guess_type(str(img_path))
        mime_type = mime_type or 'image/png'
        b64 = base64.b64encode(img_path.read_bytes()).decode('utf-8')
        return f"data:{mime_type};base64,{b64}"

    def _is_base64_image(self, s: str) -> bool:
        return s.startswith('data:image/')

    # -------- 文本问答 --------
    async def chat_text(
        self,
        system_prompt: str,
        user_question: str,
        history: Optional[List[Dict]] = None,
        model: Optional[str] = None,
    ) -> str:
        """纯文本问答"""
        messages: List[Dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_question})

        return await self._call_api(messages, model or self.text_model)

    # -------- 多模态问答（图文一起发给 LLM）--------
    async def chat_multimodal(
        self,
        system_prompt: str,
        user_question: str,
        images: List[str],  # 可以是本地路径 或 data URL
        history: Optional[List[Dict]] = None,
        model: Optional[str] = None,
    ) -> str:
        """
        多模态问答：用户问题 + 图片一起发送给 Vision 模型

        images: 图片路径列表或 base64 data URL 列表
        """
        messages: List[Dict] = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)

        # 构造 user message（多模态格式）
        content_parts: List[Dict] = []
        content_parts.append({"type": "text", "text": user_question})

        for img in images:
            if self._is_base64_image(img):
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": img}
                })
            else:
                data_url = self._encode_image(img)
                content_parts.append({
                    "type": "image_url",
                    "image_url": {"url": data_url}
                })

        messages.append({"role": "user", "content": content_parts})

        return await self._call_api(messages, model or self.vision_model)

    # -------- 核心 API 调用 --------
    async def _call_api(self, messages: List[Dict], model: str) -> str:
        """调用 Chat Completions API"""
        url = self._get_url("chat/completions")

        payload = {
            "model": model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        logger.info(f"调用 LLM: model={model}, messages_count={len(messages)}")

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            answer = data["choices"][0]["message"]["content"]
            logger.info(f"LLM 返回成功，长度={len(answer)}")
            return answer

        except httpx.HTTPStatusError as e:
            error_detail = e.response.text[:500] if e.response else ""
            logger.error(f"LLM API 错误: {e}, detail={error_detail}")
            raise
        except Exception as e:
            logger.error(f"LLM 调用异常: {e}")
            raise
