"""解析层的公共数据结构：不管上游是 MinerU 还是内置兜底解析器，都产出同一个 ParsedDoc。

这样下游（切块 / 向量化 / 入库 / 问答）完全不感知解析器差异，也满足「后端通过 MinerU
解析 PDF 进行存储」的同时保留了没有 MinerU 环境时的可运行性。

ParsedDoc 结构
--------------
    blocks: 按阅读顺序排列的语义块
        kind = text  | table | image
        text = 文本内容 / 表格的 markdown / 图片的题注（可能为空）
        image_ref = 图片在解析结果里的引用（MinerU 的 images/xxx.jpg，或内置的 p1_img2）
    images: 图片清单，path 已落盘到 data/images/{doc_id}/
    markdown: 整篇 markdown（MinerU 原生提供；内置解析器按 blocks 拼一个近似的）
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

KIND_TEXT = "text"
KIND_TABLE = "table"
KIND_IMAGE = "image"


@dataclass
class Block:
    kind: str
    page: int = 0
    text: str = ""
    image_ref: str = ""
    bbox: Optional[Sequence[float]] = None
    order: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "page": self.page,
            "text": self.text,
            "image_ref": self.image_ref,
            "bbox": list(self.bbox) if self.bbox else None,
            "order": self.order,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any], order: int = 0) -> "Block":
        return cls(
            kind=d.get("kind", KIND_TEXT),
            page=int(d.get("page") or 0),
            text=d.get("text") or "",
            image_ref=d.get("image_ref") or "",
            bbox=d.get("bbox"),
            order=int(d.get("order", order)),
        )


@dataclass
class ImageAsset:
    ref: str
    page: int = 0
    path: str = ""            # 绝对路径（落盘后）
    caption: str = ""
    width: int = 0
    height: int = 0
    source: str = ""          # mineru / pdf_local

    def to_dict(self) -> Dict[str, Any]:
        return {"ref": self.ref, "page": self.page, "path": self.path, "caption": self.caption,
                "width": self.width, "height": self.height, "source": self.source}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ImageAsset":
        return cls(
            ref=d.get("ref", ""), page=int(d.get("page") or 0), path=d.get("path", ""),
            caption=d.get("caption", ""), width=int(d.get("width") or 0),
            height=int(d.get("height") or 0), source=d.get("source", ""),
        )


@dataclass
class ParsedDoc:
    doc_id: str
    filename: str
    parser: str                       # mineru | pdf_local
    pages: int = 0
    blocks: List[Block] = field(default_factory=list)
    images: List[ImageAsset] = field(default_factory=list)
    markdown: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)

    # ---------------------------------------------------------------- 统计
    def n_text(self) -> int:
        return sum(1 for b in self.blocks if b.kind == KIND_TEXT)

    def n_tables(self) -> int:
        return sum(1 for b in self.blocks if b.kind == KIND_TABLE)

    def n_images(self) -> int:
        return len(self.images)

    def n_chars(self) -> int:
        return sum(len(b.text or "") for b in self.blocks)

    def image_by_ref(self, ref: str) -> Optional[ImageAsset]:
        for im in self.images:
            if im.ref == ref:
                return im
        return None

    def stats(self) -> Dict[str, Any]:
        return {
            "parser": self.parser,
            "pages": self.pages,
            "blocks": len(self.blocks),
            "text_blocks": self.n_text(),
            "table_blocks": self.n_tables(),
            "images": self.n_images(),
            "chars": self.n_chars(),
        }

    # ---------------------------------------------------------------- 落盘
    def artifact_dir(self) -> Any:
        import config

        return config.PARSED_DIR / self.doc_id

    def save(self) -> str:
        """把解析产物写成 parsed/{doc_id}/blocks.json + full.md，供后续阶段与人工排查。

        Kafka 里只传目录路径，不传大体积正文 —— 见 messages.py 的设计说明。
        """
        import json
        from pathlib import Path

        d = Path(self.artifact_dir())
        d.mkdir(parents=True, exist_ok=True)
        payload = {
            "doc_id": self.doc_id,
            "filename": self.filename,
            "parser": self.parser,
            "pages": self.pages,
            "meta": self.meta,
            "blocks": [b.to_dict() for b in self.blocks],
            "images": [i.to_dict() for i in self.images],
        }
        (d / "blocks.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        (d / "full.md").write_text(self.markdown or self.to_markdown(), encoding="utf-8")
        return str(d)

    @classmethod
    def load(cls, artifact_dir: str) -> "ParsedDoc":
        import json
        from pathlib import Path

        d = Path(artifact_dir)
        payload = json.loads((d / "blocks.json").read_text(encoding="utf-8"))
        md_path = d / "full.md"
        return cls(
            doc_id=payload.get("doc_id", ""),
            filename=payload.get("filename", ""),
            parser=payload.get("parser", ""),
            pages=int(payload.get("pages") or 0),
            blocks=[Block.from_dict(b, i) for i, b in enumerate(payload.get("blocks") or [])],
            images=[ImageAsset.from_dict(i) for i in payload.get("images") or []],
            markdown=md_path.read_text(encoding="utf-8") if md_path.exists() else "",
            meta=payload.get("meta") or {},
        )

    # ---------------------------------------------------------------- markdown
    def to_markdown(self) -> str:
        """按 blocks 拼 markdown（内置解析器没有原生 md 时用；也是图片引用重写的基线）。"""
        lines: List[str] = []
        page = None
        for b in self.blocks:
            if b.page != page:
                page = b.page
                lines.append(f"\n## 第 {page} 页\n")
            if b.kind == KIND_IMAGE:
                ref = f"images/{b.image_ref}" if b.image_ref and "/" not in b.image_ref else b.image_ref
                lines.append(f"![{b.text or '图片'}]({ref})")
                if b.text:
                    lines.append(f"\n> 题注：{b.text}\n")
            elif b.kind == KIND_TABLE:
                lines.append(b.text.strip())
                lines.append("")
            else:
                lines.append(b.text.strip())
                lines.append("")
        return "\n".join(lines).strip() + "\n"


# ---------------------------------------------------------------------------
# 文本清洗
# ---------------------------------------------------------------------------
_STRUCT_LINE = re.compile(r"^(#{1,6}\s|[-*+]\s|\d+\.\s|[|>•·])")
_SENT_TAIL = re.compile(r"[。！？；：.!?;:]$")


def _is_cjk(ch: str) -> bool:
    """中日韩汉字。"""
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF
            or 0xF900 <= o <= 0xFAFF or 0x20000 <= o <= 0x2A6DF)


def _is_cjk_punct(ch: str) -> bool:
    """中文标点（，。「」（）：；！？、 等）——两侧都不该再加空格。"""
    o = ord(ch)
    return (0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF
            or ch in "“”‘’…—·《》〈〉【】")


def line_separator(prev_ch: str, next_ch: str, prev_line_filled: bool = True,
                   prev_tail: str = "", next_head: str = "") -> str:
    """PDF 折行处该补什么分隔符。

    PDF 换行时**行尾空格会被吃掉**，所以拼接时必须把空格还原出来，否则会拼出
    ``mmrag.d`` + ``oc.status`` → ``mmrag.doc.status``（对）或 ``问答`` + ``Worker``
    → ``问答 Worker``（中文排版惯例，中英边界原本就有一个空格）。

    规则
    ----
    1. 两侧都是汉字/假名 → 不加空格（中文词内不会有空格）；
    2. 一侧是中文标点 → 不加空格（``（RAG）`` + ``系统`` 不能变 ``（RAG） 系统``）；
    3. 汉字与拉丁字母的边界 → 补一个空格（中文技术文档习惯在英文词两侧留空）；
    4. 两侧都是拉丁字母时，先看词形再看版式：
       ``prev_tail`` / ``next_head`` 是断点两侧的拉丁词尾/词首。含 ``. _ / -`` 或数字
       （``mmrag.doc.status``、``bge-small-zh-v1.5`` 这类名字）被切断的概率极高 → 不加空格；
       词尾只剩 1~2 个字母也不可能是完整单词 → 不加空格；
       否则按版式判断：上一行**排到右边距**说明是在空格处折行 → 补空格，没排满则是词内切断 → 不补。
    """
    if not prev_ch or not next_ch:
        return ""
    a_han, b_han = _is_cjk(prev_ch), _is_cjk(next_ch)
    a_punct, b_punct = _is_cjk_punct(prev_ch), _is_cjk_punct(next_ch)
    if a_punct or b_punct:
        return ""
    if a_han and b_han:
        return ""
    if a_han != b_han:
        return " "
    # 纯拉丁边界
    name_chars = "._/-"
    if prev_tail and next_head:
        if any(c in name_chars or c.isdigit() for c in prev_tail) and len(prev_tail) >= 2:
            return ""
        if any(c in name_chars for c in next_head):
            return ""
    if len(prev_tail) <= 2:
        return ""
    return " " if prev_line_filled else ""


LATIN_TAIL = re.compile(r"[A-Za-z0-9_.\-/]+$")
LATIN_HEAD = re.compile(r"[A-Za-z0-9_.\-/]+")


def join_two_lines(prev_text: str, next_text: str, prev_line_filled: bool = True) -> str:
    """把被折开的两行拼回一行（内部用 ``line_separator`` 决定分隔符）。"""
    a = (prev_text or "").rstrip()
    b = (next_text or "").strip()
    if not a:
        return b
    if not b:
        return a
    prev_ch, next_ch = a[-1], b[0]
    if prev_ch == "-" and next_ch.isalpha() and not _SENT_TAIL.search(a):
        # 连字符有两种：英文断词（hot- / el → hotel，要去掉连字符）
        #               名字本身带的连字符（bge-small- / zh → 必须保留连字符）
        m = LATIN_TAIL.search(a)
        run = m.group(0)[:-1] if m else ""      # 连字符之前的那截
        name_like = any(c in run for c in "._/-") or any(c.isdigit() for c in run)
        return a + b if name_like else a[:-1] + b
    tail = LATIN_TAIL.search(a)
    head = LATIN_HEAD.match(b)
    sep = line_separator(prev_ch, next_ch, prev_line_filled,
                         tail.group(0) if tail else "", head.group(0) if head else "")
    return a + sep + b


def join_wrapped_lines(lines: List[str]) -> List[str]:
    """把 PDF 里被排版换行切断的一行行文本，重新拼成完整段落（无几何信息版）。

    空行 = 段落分隔；``#`` / ``-`` / ``|`` / 数字编号 开头的结构行不拼接。
    有版式信息的内置解析器走 ``local_pdf._join_lines``（判据 4 更准），这里是 MinerU 文本块的兜底。
    """
    out: List[str] = []
    buf = ""

    def flush() -> None:
        nonlocal buf
        if buf.strip():
            out.append(buf.strip())
        buf = ""

    for raw in lines:
        ln = raw.strip()
        if not ln:
            flush()
            continue
        if _STRUCT_LINE.match(ln):
            flush()
            out.append(ln)
            continue
        buf = join_two_lines(buf, ln, prev_line_filled=True)
    flush()
    return out


def clean_text(s: str) -> str:
    """去掉 PDF 里常见的噪音（软连字符、零宽字符、nbsp），并把换行拼接成段落。"""
    if not s:
        return ""
    s = s.replace("­", "").replace("﻿", "").replace("​", "")
    s = s.replace(" ", " ").replace(" ", " ")
    return "\n".join(join_wrapped_lines(s.split("\n")))


# 图片题注的开头模式（中英混排），用于把题注文本和正文区分开
CAPTION_PREFIXES = (
    "图", "图表", "插图", "表", "画像",
    "fig", "figure", "chart", "image", "img", "tab", "table", "exhibit",
)


def looks_like_caption(text: str) -> bool:
    t = (text or "").strip().lower()
    if not t or len(t) > 120:
        return False
    return any(t.startswith(p) for p in CAPTION_PREFIXES)
