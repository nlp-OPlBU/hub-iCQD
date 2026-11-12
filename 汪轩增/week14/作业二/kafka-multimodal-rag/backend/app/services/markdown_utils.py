"""Markdown 中图片链接的处理。

MinerU 输出的 markdown 里是 ![alt](images/xxx.jpg) 这种相对路径，
入库前统一改写为 /static/processed/<相对路径>，这样前端能直接渲染。
"""

import re
from pathlib import Path
from urllib.parse import unquote

from ..config import Settings

IMAGE_MD_RE = re.compile(
    r"!\[(?P<alt>[^\]]*)\]\((?P<src>[^)\s]+)(?:\s+\"[^\"]*\")?\)"
)

_REF_LINE_RE = re.compile(r"^\[\d+\]")


def strip_images(text: str) -> str:
    """去掉 markdown 图片语法，只保留文字（用于文本 embedding 和摘要）。"""
    cleaned = IMAGE_MD_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def extract_image_srcs(text: str) -> list[str]:
    """按出现顺序提取图片地址并去重。"""
    seen: set[str] = set()
    result: list[str] = []
    for match in IMAGE_MD_RE.finditer(text):
        src = match.group("src")
        if src not in seen:
            seen.add(src)
            result.append(src)
    return result


def rewrite_local_images(markdown: str, markdown_dir: Path, settings: Settings) -> str:
    """把 markdown 里的本地相对图片路径转成 /static/processed/... 地址。

    - 已经是 http(s) / data / /static 开头的链接保持原样
    - 解析不到、越出 processed 目录的链接也保持原样
    """

    processed_root = settings.processed_path.resolve()

    def _replace(match: re.Match) -> str:
        alt = match.group("alt")
        src = match.group("src")

        if src.startswith(("http://", "https://", "data:", settings.static_prefix + "/")):
            return match.group(0)

        raw = unquote(src.split("?")[0].split("#")[0])
        candidate = (markdown_dir / raw).resolve()
        try:
            relative = candidate.relative_to(processed_root)
        except ValueError:
            return match.group(0)

        if not candidate.is_file():
            return match.group(0)

        static_url = f"{settings.static_processed_prefix}/{relative.as_posix()}"
        return f"![{alt}]({static_url})"

    return IMAGE_MD_RE.sub(_replace, markdown)


def is_reference_line(line: str) -> bool:
    """类似 [1] xxx 的参考文献行，检索价值低，切块时过滤。"""
    return bool(_REF_LINE_RE.match(line.strip()))
