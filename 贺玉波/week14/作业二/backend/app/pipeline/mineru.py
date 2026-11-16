"""MinerU 解析适配层。

把外部 CLI 收敛成 ``parse_document`` 一个函数：换解析器只改这里，上层不感知命令行细节。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import get_settings, resolve_path
from app.core.exceptions import ErrorCode
from app.core.logging import get_logger, log_event

logger = get_logger("app.pipeline.mineru")

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
# MinerU 4.x 的 markdown 按页打标：`<!-- page 3 of 14 -->`
PAGE_MARK = re.compile(r"<!--\s*page\s+(\d+)(?:\s*of\s+\d+)?\s*-->", re.IGNORECASE)
MIN_IMAGE_SIDE = 64  # 更小的多是分割线/图标，进向量库只会污染召回


class ParseError(RuntimeError):
    def __init__(self, message: str, code: ErrorCode = ErrorCode.PARSE_FAILED) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ParseResult:
    output_dir: Path
    markdown_path: Path | None = None
    image_dir: Path | None = None
    images: list[Path] = field(default_factory=list)
    pages: list[tuple[int, str]] = field(default_factory=list)
    page_count: int | None = None
    duration_ms: int = 0


def load_pages(output_dir: Path) -> list[tuple[int, str]]:
    """从 MinerU 的 ``*_content_list.json`` 还原「页码 → 该页文本」。

    页码取 ``page_idx + 1``（MinerU 从 0 开始，引用要给人看）。
    没有 content_list 时返回空列表，上层退回整篇切块、页码留空。
    """
    candidates = sorted(output_dir.rglob("*_content_list.json"))
    if not candidates:
        return []
    try:
        items = json.loads(candidates[0].read_text(encoding="utf-8"))
    except Exception as exc:
        log_event(logger, "content_list_invalid", level=30, file=str(candidates[0]), error=str(exc))
        return []

    pages: dict[int, list[str]] = {}
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("table_body") or "").strip()
        page = item.get("page_idx")
        if not text or not isinstance(page, int):
            continue
        pages.setdefault(page, []).append(text)
    return [(page + 1, "\n\n".join(blocks)) for page, blocks in sorted(pages.items())]


def pages_from_markdown(markdown: str) -> list[tuple[int, str]]:
    """按 MinerU 4.x 的 ``<!-- page N of M -->`` 标记切出「页码 → 该页文本」。"""
    marks = list(PAGE_MARK.finditer(markdown))
    if not marks:
        return []
    pages: list[tuple[int, str]] = []
    for index, mark in enumerate(marks):
        start = mark.end()
        end = marks[index + 1].start() if index + 1 < len(marks) else len(markdown)
        text = markdown[start:end].strip()
        if text:
            pages.append((int(mark.group(1)), text))
    return pages


def extract_images(pdf_path: Path, image_dir: Path) -> list[Path]:
    """MinerU 4.x 的 flash 档不落地图片文件，这里用 PyMuPDF 按页抽原图补上。

    文件名带页码（``p003_02.png``），顺序稳定，便于人工核对与前端展示。
    """
    try:
        import pymupdf
    except ImportError:  # pragma: no cover - 兼容旧版 PyMuPDF
        import fitz as pymupdf  # type: ignore

    image_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    seen: set[int] = set()
    try:
        with pymupdf.open(str(pdf_path)) as pdf:
            for page_index in range(pdf.page_count):
                page = pdf[page_index]
                for order, info in enumerate(page.get_images(full=True), start=1):
                    xref = info[0]
                    if xref in seen:
                        continue
                    try:
                        raw = pdf.extract_image(xref)
                    except Exception:  # 单张坏图解不出来就跳过，不影响整篇
                        continue
                    if not raw or raw.get("width", 0) < MIN_IMAGE_SIDE or raw.get("height", 0) < MIN_IMAGE_SIDE:
                        continue
                    seen.add(xref)
                    suffix = str(raw.get("ext", "png")).lower()
                    if f".{suffix}" not in IMAGE_SUFFIXES:
                        suffix = "png"
                    target = image_dir / f"p{page_index + 1:03d}_{order:02d}.{suffix}"
                    target.write_bytes(raw["image"])
                    saved.append(target)
    except Exception as exc:
        log_event(logger, "image_extract_failed", level=30, ref_id=str(pdf_path), error=str(exc))
    return saved


def count_pages(pdf_path: Path) -> int | None:
    """用 PyMuPDF 取页数；失败不影响主流程。"""
    try:
        try:
            import pymupdf as fitz  # PyMuPDF >= 1.24 的新包名
        except ImportError:  # pragma: no cover - 兼容旧版本
            import fitz  # type: ignore

        with fitz.open(str(pdf_path)) as pdf:
            return pdf.page_count
    except Exception as exc:  # 页数只是元数据，读不到就算了
        log_event(logger, "page_count_failed", level=30, error=str(exc))
        return None


def _pick_markdown(output_dir: Path) -> Path | None:
    candidates = sorted(output_dir.rglob("*.md"), key=lambda item: (len(item.parts), str(item)))
    return candidates[0] if candidates else None


def parse_document(doc_id: str, pdf_path: Path, output_dir: Path) -> ParseResult:
    """调用 MinerU 解析 PDF，返回 Markdown 与图片清单。

    任何异常都转成 ``ParseError``，由 worker 决定重试还是进 DLQ。
    """
    settings = get_settings().mineru
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)

    if not pdf_path.exists():
        raise ParseError(f"源文件不存在: {pdf_path}")

    page_count = count_pages(pdf_path)
    if page_count is not None and page_count > settings.max_pages:
        raise ParseError(f"页数 {page_count} 超过上限 {settings.max_pages}", ErrorCode.PDF_TOO_LARGE)

    # MinerU 4.x 的 CLI：parse <pdf> -o <md> --tier ... --pages all
    md_path = output_dir / f"{doc_id}.md"
    cmd = [
        settings.bin,
        "parse",
        str(pdf_path),
        "-o",
        str(md_path),
        "--tier",
        settings.tier,
        "--pages",
        "all",
        "--format",
        "markdown",
        "--wait",
        str(max(60, settings.timeout - 60)),
    ]
    env = {
        **os.environ,
        "MINERU_DEVICE_MODE": settings.device,
        "MINERU_MODEL_SOURCE": settings.model_source,
    }

    log_path = resolve_path("logs/mineru") / f"{doc_id}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=settings.timeout, env=env)
    except FileNotFoundError as exc:
        raise ParseError(f"MinerU 未安装或不在 PATH: {settings.bin}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ParseError(f"MinerU 解析超时（>{settings.timeout}s）", ErrorCode.PARSE_TIMEOUT) from exc

    log_path.write_text(f"$ {' '.join(cmd)}\n\n{proc.stdout}\n{proc.stderr}", encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        raise ParseError(f"MinerU 退出码 {proc.returncode}: {' | '.join(tail)}")

    # flash 档把 ``-o`` 当文件路径写单个 md；老档位/别的版本可能落在子目录里，兜底找一遍
    markdown_path = md_path if md_path.exists() else _pick_markdown(output_dir)
    if markdown_path is None or not markdown_path.exists():
        raise ParseError("MinerU 未产出 Markdown")
    markdown = markdown_path.read_text(encoding="utf-8", errors="ignore")

    # 优先用 content_list（信息最全）；没有就按 md 里的页码标记切
    pages = load_pages(output_dir) or pages_from_markdown(markdown)
    image_dir = output_dir / "images"
    images = extract_images(pdf_path, image_dir)
    result = ParseResult(
        output_dir=output_dir,
        markdown_path=markdown_path,
        image_dir=image_dir if images else None,
        images=images,
        pages=pages,
        page_count=page_count,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    log_event(
        logger,
        "mineru_done",
        ref_id=doc_id,
        pages=page_count,
        paged_chars=sum(len(text) for _, text in result.pages),
        images=len(images),
        dur_ms=result.duration_ms,
        device=settings.device,  # 实测时用来确认 GPU 是否真的生效
    )
    return result
