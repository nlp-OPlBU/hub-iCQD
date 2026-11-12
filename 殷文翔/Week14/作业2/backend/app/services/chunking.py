"""Deterministic text chunks derived from MinerU Markdown output."""
from __future__ import annotations

import re
from pathlib import Path


def _paragraphs(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]


def build_chunks(*, document_id: str, version: int, filename: str, markdown: str,
                 max_chars: int = 1200) -> list[dict]:
    """Small deterministic chunker; page stays null unless MinerU exposes it."""
    chunks: list[dict] = []
    buffer = ""
    for paragraph in _paragraphs(markdown):
        candidate = f"{buffer}\n\n{paragraph}".strip() if buffer else paragraph
        if len(candidate) <= max_chars:
            buffer = candidate
            continue
        if buffer:
            chunks.append(_chunk(document_id, version, filename, len(chunks), buffer))
        buffer = paragraph
    if buffer:
        chunks.append(_chunk(document_id, version, filename, len(chunks), buffer))
    return chunks


def _chunk(document_id: str, version: int, filename: str, index: int, text: str) -> dict:
    return {
        "chunk_id": f"{document_id}:{version}:{index}",
        "document_id": document_id,
        "version": version,
        "filename": filename,
        "page": None,
        "chunk_index": index,
        "text": text,
    }


def read_markdown(directory: Path) -> str:
    files = sorted(directory.rglob("*.md"))
    if not files:
        raise FileNotFoundError("MinerU output contains no Markdown")
    return "\n\n".join(path.read_text(encoding="utf-8", errors="replace") for path in files)
