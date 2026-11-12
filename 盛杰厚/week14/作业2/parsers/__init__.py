"""解析层入口：优先 MinerU，必要时退回内置解析器。

    from parsers import parse_pdf
    doc = parse_pdf(pdf_path, doc_id, filename, progress_cb)

规则（可用 .env 调整）：
* MinerU 服务可用 → 用 MinerU（``parser="mineru"``）；
* MinerU 不可用：
  - ``MINERU_STRICT=1`` → 抛 ParseUnavailable，文档判「解析失败」；
  - 否则 → 内置 pdfplumber/pypdfium2 解析器（``parser="pdf_local"``），
    并在状态消息与文档行里**如实写明用了哪个解析器**（不静默降级）。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

import config
from parsers.common import Block, ImageAsset, ParsedDoc  # re-export
from parsers.common import KIND_IMAGE, KIND_TABLE, KIND_TEXT


class ParseUnavailable(RuntimeError):
    """MinerU 不可用且不允许降级。"""


def mineru_probe(force: bool = False) -> Dict[str, Any]:
    from parsers import mineru

    if not config.MINERU_BASE_URL:
        return {"ok": False, "detail": "MINERU_BASE_URL 未配置"}
    info = mineru.probe(force=force)
    info["url"] = f"{config.MINERU_BASE_URL}{config.MINERU_API_PATH}"
    info["backend"] = config.MINERU_BACKEND
    return info


def parse_pdf(
    pdf_path: str,
    doc_id: str,
    filename: str = "",
    progress_cb: Optional[Callable[[float, str], None]] = None,
    force_parser: str = "",
) -> ParsedDoc:
    """解析 PDF -> ParsedDoc。force_parser ∈ {'mineru', 'pdf_local', ''}。"""
    want = (force_parser or getattr(config, "PREFERRED_PARSER", "") or "").lower()
    used_mineru = False
    err: Optional[Exception] = None

    if want in ("", "mineru", "auto"):
        info = mineru_probe()
        if info.get("ok"):
            from parsers import mineru

            try:
                doc = mineru.parse(pdf_path, doc_id, filename, progress_cb)
                used_mineru = True
                if doc.blocks or doc.images:
                    return doc
                err = RuntimeError("MinerU 返回了空结果（可能是加密 PDF 或全矢量页）")
            except Exception as exc:  # noqa: BLE001
                err = exc
        elif want == "mineru" or config.MINERU_STRICT:
            raise ParseUnavailable(
                f"MinerU 服务不可用（{config.MINERU_BASE_URL}）：{info.get('detail')}"
            )
        elif want == "":
            if progress_cb:
                try:
                    progress_cb(8, f"MinerU 不可用（{info.get('detail')}），改用内置解析器")
                except Exception:  # noqa: BLE001
                    pass

    if not used_mineru:
        from parsers import local_pdf

        doc = local_pdf.parse(pdf_path, doc_id, filename, progress_cb)
        if not doc.blocks and not doc.images:
            raise RuntimeError(
                "内置解析器没有抽到任何内容：可能是扫描件（整页都是图）或加密 PDF。"
                "这种 PDF 需要 MinerU 的 OCR 后端（vlm / hybrid-auto-engine）。"
            )
        if err is not None and doc.meta.get("mineru_error") is None:
            doc.meta["mineru_error"] = f"{type(err).__name__}: {err}"
        return doc
    raise RuntimeError(str(err) if err else "解析失败")


__all__ = ["parse_pdf", "mineru_probe", "ParsedDoc", "Block", "ImageAsset",
           "KIND_TEXT", "KIND_TABLE", "KIND_IMAGE", "ParseUnavailable"]
