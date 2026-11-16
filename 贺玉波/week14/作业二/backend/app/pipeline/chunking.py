"""Markdown 切块。

策略：先按空行分段，超长段落再按句子切，最后按块长上限聚合；
相邻块保留尾部重叠，避免答案正好卡在边界上被割裂。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.enums import ChunkKind

PARAGRAPH_SPLIT = re.compile(r"\n\s*\n")
SENTENCE_SPLIT = re.compile(r"(?<=[。！？!?；;])")


@dataclass
class TextChunk:
    idx: int
    kind: str
    text: str
    page_no: int | None = None


def _split_long_paragraph(paragraph: str, max_chars: int) -> list[str]:
    """单段超长时按句切；仍有超长句就硬切，保证每片不超过 max_chars。"""
    parts: list[str] = []
    buffer = ""
    for sentence in (item for item in SENTENCE_SPLIT.split(paragraph) if item.strip()):
        if len(sentence) > max_chars:
            if buffer:
                parts.append(buffer)
                buffer = ""
            parts.extend(sentence[i : i + max_chars] for i in range(0, len(sentence), max_chars))
            continue
        if len(buffer) + len(sentence) > max_chars:
            parts.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
    if buffer:
        parts.append(buffer)
    return parts


def _kind_of(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("#"):
        return ChunkKind.HEADING.value
    if stripped.startswith("|"):
        return ChunkKind.TABLE.value
    return ChunkKind.PARAGRAPH.value


def split_markdown(
    text: str,
    *,
    max_chars: int = 800,
    overlap: int = 100,
    page_no: int | None = None,
) -> list[TextChunk]:
    """按页调用时传入 ``page_no``，引用才能指回原文页码。"""
    if max_chars <= 0:
        raise ValueError("max_chars 必须为正数")
    overlap = max(0, min(overlap, max_chars // 2))

    pieces: list[str] = []
    for paragraph in (item.strip() for item in PARAGRAPH_SPLIT.split(text)):
        if not paragraph:
            continue
        pieces.extend([paragraph] if len(paragraph) <= max_chars else _split_long_paragraph(paragraph, max_chars))

    merged: list[str] = []
    buffer = ""
    for piece in pieces:
        if not buffer:
            buffer = piece
        elif len(buffer) + 1 + len(piece) <= max_chars:
            buffer = f"{buffer}\n{piece}"
        else:
            merged.append(buffer)
            tail = buffer[-overlap:] if overlap else ""
            buffer = f"{tail}\n{piece}" if tail else piece
    if buffer:
        merged.append(buffer)

    return [
        TextChunk(idx=index, kind=_kind_of(chunk), text=chunk, page_no=page_no)
        for index, chunk in enumerate(merged)
        if chunk.strip()
    ]
