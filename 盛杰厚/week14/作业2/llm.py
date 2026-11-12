"""大模型接入 —— **阿里百炼（DashScope）OpenAI 兼容模式**。

为什么用兼容模式而不是 dashscope SDK：接口形状与 OpenAI 一致，``image_url`` 内容块
就能把「图 + 问」一起送进模型，换成别的兼容网关（SiliconFlow / vLLM / 自建）只改 .env
两行，不用改代码。

两个模型（.env 可改）
--------------------
* ``LLM_MODEL``      默认 ``qwen-vl-max``  —— **多模态**：用户问题带图、或检索到文档配图时用它
* ``LLM_TEXT_MODEL`` 默认 ``qwen-plus``    —— 纯文本问答时用它，更省 token

百炼对图片输入的硬性要求（这里都做了处理）：
* 边长必须 > 28px 且 <= 数千像素：过小会报 ``invalid_parameter_error``，所以 ``_prep_image()``
  会把太小的图等比放大、太大的图等比缩小；
* 单图 base64 后不宜过大：超过 ~3MB 会重编码为 JPEG；
* PNG 透明通道在部分模型上不被接受：统一转 RGB（除非确实需要 alpha）。

``DASHSCOPE_API_KEY`` 没配时自动进 **mock 模式**：用检索到的片段拼一份结构化回答
（**仍然带图片 markdown**），这样「图文混排输出」在前端渲染上照样能验证，
只是答案内容不是真模型生成的 —— /api/health 与回答下方都会明确标注 mock。
"""
from __future__ import annotations

import base64
import io
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import config

_LOCK = threading.RLock()
_MAX_IMAGE_BYTES = 3_500_000   # base64 前的原始字节上限
_MAX_EDGE = 1568               # 视觉模型常用的分辨率上限


class LLMError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 图片 -> 模型可接受的 data URI
# ---------------------------------------------------------------------------
def _prep_image(src: Any) -> Tuple[str, str]:
    """返回 (mime, data_uri)。src 可以是本地路径、Path、http(s) URL 或 data URI。"""
    if isinstance(src, str) and src.startswith("data:"):
        mime = src.split(":", 1)[1].split(";", 1)[0]
        return mime, src
    if isinstance(src, str) and src.startswith(("http://", "https://")):
        # 百炼支持直接给 URL，省一次上传
        return "image/url", src

    path = Path(str(src))
    if not path.exists():
        raise LLMError(f"图片不存在: {path}")
    raw = path.read_bytes()
    if len(raw) > 12_000_000:
        raise LLMError(f"图片过大({len(raw) / 1e6:.1f}MB)：{path.name}")

    try:
        from PIL import Image

        img = Image.open(io.BytesIO(raw))
        img.load()
        w, h = img.size
        if w == 0 or h == 0:
            raise LLMError(f"图片尺寸异常: {path}")
        scale = 1.0
        # 过小：百炼要求宽高均 > 28px（MinerU 抽出的小图标常踩这个坑）
        if min(w, h) < config.LLM_IMAGE_MIN_EDGE:
            scale = (config.LLM_IMAGE_MIN_EDGE + 8) / max(1, min(w, h))
        elif max(w, h) > _MAX_EDGE:
            scale = _MAX_EDGE / max(w, h)
        if abs(scale - 1.0) > 0.01:
            new = (max(config.LLM_IMAGE_MIN_EDGE + 1, int(round(w * scale))),
                   max(config.LLM_IMAGE_MIN_EDGE + 1, int(round(h * scale))))
            img = img.resize(new, Image.LANCZOS)
            has_alpha = img.mode in ("RGBA", "LA", "P") and "transparency" in img.info
            img = img.convert("RGBA" if has_alpha else "RGB")
            buf = io.BytesIO()
            img.save(buf, "PNG" if has_alpha else "JPEG", quality=90)
            raw, mime = buf.getvalue(), ("image/png" if has_alpha else "image/jpeg")
        else:
            mime = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp",
                    "GIF": "image/gif", "BMP": "image/bmp"}.get(img.format or "", "image/jpeg")
            if img.mode not in ("RGB", "RGBA", "L") or len(raw) > _MAX_IMAGE_BYTES:
                img = img.convert("RGB")
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=88)
                raw, mime = buf.getvalue(), "image/jpeg"
    except LLMError:
        raise
    except Exception as exc:  # noqa: BLE001  EMF/WMF 等 PIL 打不开的格式
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp"}.get(
            path.suffix.lower(), "image/jpeg")
        print(f"[llm] 图片预处理跳过（{type(exc).__name__}: {exc}），直接原样编码 {path.name}",
              flush=True)

    return mime, "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def image_part(src: Any) -> Dict[str, Any]:
    """OpenAI 兼容 content 里的 image_url 块。"""
    mime, uri = _prep_image(src)
    if mime == "image/url":
        return {"type": "image_url", "image_url": {"url": uri, "detail": "high"}}
    return {"type": "image_url", "image_url": {"url": uri, "detail": "high"}}


def text_part(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def build_messages(
    system: str,
    question: str,
    images: Sequence[Any] = (),
    history: Sequence[Dict[str, Any]] = (),
    context_block: str = "",
) -> List[Dict[str, Any]]:
    """把「系统提示 + 多轮历史 + 检索上下文 + 本轮问题(含图)」拼成 messages。

    本轮问题里的图片与文字在 **同一个 user 消息** 内 —— 这正是「图问一起送到 LLM」。
    """
    msgs: List[Dict[str, Any]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    for h in history:
        role = h.get("role")
        if role in ("user", "assistant") and h.get("content"):
            content = h["content"]
            if role == "user" and h.get("images"):
                parts = [text_part(content)] + [image_part(p) for p in h["images"]]
                content = parts  # 历史里的图也带上（多轮追问「这张图的第三部分」）
            msgs.append({"role": role, "content": content})
    user_parts: List[Dict[str, Any]] = []
    if context_block:
        user_parts.append(text_part(context_block))
    user_parts.append(text_part(question))
    for im in images:
        user_parts.append(image_part(im))
    msgs.append({"role": "user", "content": user_parts})
    return msgs


# ---------------------------------------------------------------------------
class LLM:
    def __init__(self, force_mock: Optional[bool] = None):
        self.mock = config.LLM_MOCK if force_mock is None else bool(force_mock)
        self.base_url = config.LLM_BASE_URL
        self.vision_model = config.LLM_MODEL
        self.text_model = config.LLM_TEXT_MODEL
        self.calls = 0
        self.errors = 0
        self.last_error = ""
        self._client = None
        self.usage: Dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}

    # ------------------------------------------------------------ 状态
    def info(self) -> Dict[str, Any]:
        return {
            "mock": self.mock,
            "base_url": self.base_url,
            "vision_model": self.vision_model,
            "text_model": self.text_model,
            "has_key": bool(config.DASHSCOPE_API_KEY),
            "calls": self.calls,
            "errors": self.errors,
            "last_error": self.last_error,
            "usage": dict(self.usage),
        }

    def pick_model(self, has_images: bool) -> str:
        return self.vision_model if has_images else self.text_model

    def _ensure_client(self):
        if self._client is not None:
            return self._client
        with _LOCK:
            if self._client is None:
                if self.mock:
                    raise LLMError("mock 模式不需要客户端")
                if not config.DASHSCOPE_API_KEY:
                    raise LLMError("未配置 DASHSCOPE_API_KEY（阿里百炼 API Key）")
                from openai import OpenAI

                self._client = OpenAI(
                    api_key=config.DASHSCOPE_API_KEY,
                    base_url=self.base_url,
                    timeout=config.LLM_TIMEOUT,
                    max_retries=1,
                )
        return self._client

    # ------------------------------------------------------------ 调用
    def stream(self, messages: List[Dict[str, Any]], model: str = "",
               max_tokens: Optional[int] = None, temperature: Optional[float] = None,
               mock_reply: Optional[Iterable[str]] = None) -> Iterator[str]:
        """流式生成。mock 时把 ``mock_reply`` 逐段吐出来，前端体验保持一致。"""
        if self.mock:
            for piece in (mock_reply or []):
                time.sleep(0.02)
                yield piece
            return
        client = self._ensure_client()
        kwargs: Dict[str, Any] = {
            "model": model or self._model_of(messages),
            "messages": messages,
            "stream": True,
            "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
            "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
            "extra_body": {"incremental_output": True},   # 百炼兼容模式：确保增量返回
        }
        self.calls += 1
        try:
            stream = client.chat.completions.create(**kwargs)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            raise LLMError(self._friendly(exc)) from exc
        got_any = False
        for chunk in stream:
            try:
                delta = chunk.choices[0].delta.content if chunk.choices else None
            except (AttributeError, IndexError, TypeError):
                delta = None
            if delta:
                got_any = True
                yield delta
        if not got_any:
            # 有些网关 stream 下只回 usage 不回内容，兜底再非流式一次
            text = self.complete(messages, model=kwargs["model"], max_tokens=max_tokens,
                                 temperature=temperature)
            if text:
                yield text

    def complete(self, messages: List[Dict[str, Any]], model: str = "",
                 max_tokens: Optional[int] = None, temperature: Optional[float] = None) -> str:
        client = self._ensure_client()
        kwargs: Dict[str, Any] = {
            "model": model or self._model_of(messages),
            "messages": messages,
            "max_tokens": max_tokens or config.LLM_MAX_TOKENS,
            "temperature": config.LLM_TEMPERATURE if temperature is None else temperature,
        }
        last_exc: Optional[Exception] = None
        for attempt in range(3):
            try:
                self.calls += 1
                resp = client.chat.completions.create(**kwargs)
                try:
                    if resp.usage:
                        self.usage["prompt_tokens"] += int(resp.usage.prompt_tokens or 0)
                        self.usage["completion_tokens"] += int(resp.usage.completion_tokens or 0)
                except AttributeError:
                    pass
                return (resp.choices[0].message.content or "").strip()
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {exc}"
                code = getattr(exc, "status_code", None)
                if code in (429, 500, 502, 503, 504) or "timeout" in str(exc).lower():
                    time.sleep(1.2 * (attempt + 1))
                    continue
                break
        raise LLMError(self._friendly(last_exc)) if last_exc else None

    def _model_of(self, messages: List[Dict[str, Any]]) -> str:
        has_img = any(
            isinstance(m.get("content"), list) and
            any(isinstance(p, dict) and p.get("type") == "image_url" for p in m["content"])
            for m in messages
        )
        return self.pick_model(has_img)

    @staticmethod
    def _friendly(exc: Optional[Exception]) -> str:
        s = f"{type(exc).__name__}: {exc}" if exc else "未知错误"
        low = s.lower()
        if "invalidapikey" in low or "api-key" in low or "401" in low:
            return f"百炼鉴权失败，请检查 DASHSCOPE_API_KEY。原始错误：{s}"
        if "arrearage" in low or "insufficient" in low or "quota" in low:
            return f"百炼额度不足/欠费。原始错误：{s}"
        if "invalid_parameter" in low and "image" in low:
            return (f"百炼拒绝了图片输入（常见于图片小于 28px、格式不支持或过大）。原始错误：{s}")
        if "datainspection" in low or "content" in low and "polic" in low:
            return f"内容被安全策略拦截。原始错误：{s}"
        return s

    # ------------------------------------------------------------ 图片描述（离线索引用）
    def caption_image(self, image_src: Any, hint: str = "", max_chars: int = 220) -> str:
        """给文档里的图生成一段中文描述。这段描述会被 embedding，从而让**图片可被文本检索**。

        mock / 无 key / 调用失败时返回 hint（或空串），不抛异常 —— 图片描述失败不该让整篇文档解析失败。
        """
        if self.mock:
            return (hint or "")[:max_chars]
        prompt = (
            "你是文档图像标注助手。用不超过 80 个中文字描述这张来自 PDF 的图："
            "它是什么类型（流程图/架构图/柱状图/折线图/表格截图/实物照片/公式推导截图等）、"
            "画了哪些要素、能看出什么结论。只输出描述本身，不要前缀。\n"
            + (f"已知题注：{hint}\n" if hint else "")
        )
        try:
            text = self.complete(
                [{"role": "user", "content": [text_part(prompt), image_part(image_src)]}],
                model=self.vision_model, max_tokens=220, temperature=0.1,
            )
            return (text or hint or "").strip()[:max_chars]
        except Exception as exc:  # noqa: BLE001
            print(f"[llm] 图片描述失败（保留原题型）：{type(exc).__name__}: {exc}", flush=True)
            return (hint or "")[:max_chars]


# ---------------------------------------------------------------------------
# mock 回答：不联网也能把「图文混排」这条链路跑完
# ---------------------------------------------------------------------------
def mock_reply_pieces(question: str, hits: Sequence[Dict[str, Any]], n_images: int) -> Iterator[str]:
    """hits: [{"source": 文档名, "page": p, "text": ..., "kind": ..., "image_url": 可选}]"""
    lines: List[str] = [
        "> ⚠️ **mock 模式**：未配置 `DASHSCOPE_API_KEY`，下面这份回答是由检索结果**机械拼装**的，"
        "用来验证「Kafka → 解析 → 检索 → 图文混排输出」整条链路；配上百炼 Key 后即为真实模型回答。\n\n",
        f"针对你的问题「{question}」，从知识库中检索到 **{len(hits)}** 个相关片段"
        + (f"，其中 **{n_images}** 张配图。\n\n" if n_images else "。\n\n"),
        "**命中内容摘要**\n\n",
    ]
    for i, h in enumerate(hits[:6], 1):
        snippet = (h.get("text") or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:160] + "…"
        lines.append(f"{i}. [{h.get('source', '?')} · 第{h.get('page', 0)}页 · "
                     f"{h.get('kind', 'text')} · cos={h.get('score', 0):.3f}] {snippet}\n")
    lines.append("\n")
    imgs = [h for h in hits if h.get("kind") == "image" and h.get("image_url")]
    if imgs:
        lines.append("**相关配图**\n\n")
        for h in imgs[: config.RETRIEVAL_MAX_IMAGES]:
            lines.append(f"![{h.get('caption') or '文档配图'}]({h['image_url']})\n\n"
                         f"*图来自 `{h.get('source')}` 第 {h.get('page')} 页"
                         + (f"：{h.get('caption')}" if h.get("caption") else "") + "*\n\n")
    lines.append(
        "\n真实模型会在此基础上做**跨片段归纳**、按需要把上面这些图内嵌到正文中，"
        "并结合你上传的图片作答。\n"
    )
    for piece in lines:
        yield piece


_llm: Optional[LLM] = None


def get_llm() -> LLM:
    global _llm
    with _LOCK:
        if _llm is None:
            _llm = LLM()
            if _llm.mock:
                print("[llm] 未配置 DASHSCOPE_API_KEY -> mock 模式（回答为检索结果拼装）",
                      flush=True)
        return _llm


def reset_llm() -> None:
    global _llm
    with _LOCK:
        _llm = None


if __name__ == "__main__":  # python llm.py -> 连通性自检
    llm = get_llm()
    print(json.dumps(llm.info(), ensure_ascii=False, indent=2))
    if not llm.mock:
        try:
            print("文本模型：", llm.complete([{"role": "user", "content": "用一句话解释 RAG"}])[:120])
        except Exception as exc:  # noqa: BLE001
            print("文本调用失败:", exc)
        try:
            print("多模态：", llm.complete([{"role": "user", "content": [
                text_part("这张图里有什么？一句话。"),
                image_part("data/images/selftest/red.png") if Path("data/images/selftest/red.png").exists()
                else text_part("（无测试图，跳过）"),
            ]}])[:120])
        except Exception as exc:  # noqa: BLE001
            print("多模态调用失败:", exc)
    else:
        print("mock 自检：", "".join(mock_reply_pieces("测试问题", [
            {"source": "demo.pdf", "page": 1, "text": "示例片段", "kind": "text", "score": 0.55},
        ], 0))[:80])
