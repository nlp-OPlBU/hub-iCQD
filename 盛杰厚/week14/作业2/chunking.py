"""切块：把 ParsedDoc 的块切成「可向量化的检索单元」。

为什么要专门的切块层
--------------------
* 整篇喂 LLM 会超上下文，整页喂会稀释注意力；按语义块 + 定长滑窗是效果稳定的折中。
* 三种块区别对待：
  - **text**  ：按句子边界打包到 CHUNK_SIZE（默认 600 字），相邻块留 CHUNK_OVERLAP 重叠，
                并把**所属标题**拼进待嵌入文本 —— 这样「第 3 章 风险控制」下的段落
                被问「风险管理讲了什么」时也能召回（标题即上下文）。
  - **table** ：整块保留（markdown 表格被切开就废了），只截断到 1500 字。
  - **image** ：一块一图。嵌入的文本是「题注 + 大模型生成的图片描述」，
                于是**图片也进了文本向量空间**，问「流程图长什么样」能把图召回回来，
                答案里再把图片 URL 嵌回去 —— 这就是图文混排输出的关键。

单元里的 ``text`` 同时用于「嵌入」和「喂给 LLM 的上下文」，避免两份文本走偏。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import config
from parsers.common import KIND_IMAGE, KIND_TABLE, KIND_TEXT, ParsedDoc

# 句子边界：中文标点、英文句号+空格、换行
_SENT_SPLIT = re.compile(r"(?<=[。！？；!?;\n])|(?<=\.\s)")
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Unit:
    """一个检索单元。"""

    kind: str                       # text | table | image
    page: int
    text: str                       # 用于嵌入与展示
    image_ref: str = ""             # kind=image 时的图片引用
    heading: str = ""               # 所属标题（上下文）
    order: int = 0
    raw_text: str = field(default="", repr=False)   # 未拼标题的原文（展示用）

    def embed_text(self) -> str:
        """真正送去 embedding 的文本。"""
        if self.kind == KIND_IMAGE:
            base = self.text or "（无题注图片）"
            return f"[图片] 第{self.page}页 {base}"
        if self.heading and self.heading not in self.text:
            return f"{self.heading}\n{self.text}"
        return self.text

    def display_text(self, limit: int = 1200) -> str:
        t = self.raw_text or self.text
        return t if len(t) <= limit else t[:limit] + "…"

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "page": self.page, "text": self.text,
                "image_ref": self.image_ref, "heading": self.heading, "order": self.order}


def split_sentences(text: str) -> List[str]:
    parts = [p for p in _SENT_SPLIT.split(text) if p]
    out: List[str] = []
    buf = ""
    for p in parts:
        if len(p) > config.CHUNK_SIZE:          # 单句超长：按定长硬切
            if buf:
                out.append(buf)
                buf = ""
            for i in range(0, len(p), config.CHUNK_SIZE):
                out.append(p[i:i + config.CHUNK_SIZE])
        else:
            buf += p
    if buf.strip():
        out.append(buf.strip())
    return out


def pack_text(text: str, size: int, overlap: int) -> List[str]:
    """把长文本打包成 <= size 的片段，片段之间保留 <= overlap 的重叠。"""
    sents = split_sentences(text)
    if not sents:
        return []
    chunks: List[str] = []
    cur: List[str] = []
    cur_len = 0
    for s in sents:
        if cur and cur_len + len(s) > size:
            chunks.append("".join(cur).strip())
            # 重叠：从尾部回捞若干句，直到达到 overlap
            tail: List[str] = []
            tlen = 0
            for x in reversed(cur):
                if tlen + len(x) > overlap:
                    break
                tail.insert(0, x)
                tlen += len(x)
            cur = tail
            cur_len = tlen
        cur.append(s)
        cur_len += len(s)
    if cur:
        chunks.append("".join(cur).strip())
    return [c for c in chunks if c]


def build_units(
    doc: ParsedDoc,
    size: Optional[int] = None,
    overlap: Optional[int] = None,
    min_text_chars: int = 12,
) -> List[Unit]:
    """ParsedDoc -> [Unit]（顺序保持文档阅读顺序）。"""
    size = size or config.CHUNK_SIZE
    overlap = overlap if overlap is not None else config.CHUNK_OVERLAP
    units: List[Unit] = []
    heading = ""
    order = 0

    for block in sorted(doc.blocks, key=lambda b: (b.order, b.page)):
        if block.kind == KIND_IMAGE:
            order += 1
            units.append(Unit(
                kind=KIND_IMAGE, page=block.page, text=(block.text or "").strip(),
                image_ref=block.image_ref, heading=heading, order=order,
                raw_text=(block.text or "").strip(),
            ))
            continue

        text = (block.text or "").strip()
        if not text:
            continue

        if block.kind == KIND_TABLE:
            order += 1
            t = text if len(text) <= 1500 else text[:1500] + "…"
            units.append(Unit(kind=KIND_TABLE, page=block.page, text=t, heading=heading,
                              order=order, raw_text=t))
            continue

        # 文本块：先认标题
        m = _HEADING_RE.match(text.splitlines()[0].strip()) if text else None
        if m and len(text) < 120:
            heading = m.group(2).strip()
        pieces = pack_text(text, size, overlap) if len(text) > size else [text]
        for piece in pieces:
            piece = piece.strip()
            if len(piece) < min_text_chars:
                continue
            order += 1
            units.append(Unit(kind=KIND_TEXT, page=block.page, text=piece, heading=heading,
                              order=order, raw_text=piece))
    return units


def unit_context(unit: Unit, filename: str = "") -> str:
    """给 LLM 的上下文中一条检索结果的表示，带可追溯的来源标记。"""
    src = f"{filename} · 第{unit.page}页" if filename else f"第{unit.page}页"
    head = f"（标题：{unit.heading}）" if unit.heading and unit.kind == KIND_TEXT else ""
    if unit.kind == KIND_IMAGE:
        return f"[{src}{head}] 图片 {unit.image_ref}：{unit.text or '（无描述）'}"
    if unit.kind == KIND_TABLE:
        return f"[{src}{head}] 表格：\n{unit.text}"
    return f"[{src}{head}] {unit.text}"
