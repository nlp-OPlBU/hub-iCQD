"""把 MinerU 的 markdown 切成检索用的 chunk。"""

import re

from .markdown_utils import is_reference_line


def _is_image_line(line: str) -> bool:
    return line.lstrip().startswith("![")


def _is_heading(line: str) -> bool:
    return bool(re.match(r"^#{1,6}\s", line))


def split_markdown(text: str, max_chars: int = 800, min_chars: int = 100) -> list[str]:
    """按标题/段落/长度切块，尽量让图片跟着上文。

    - 标题前如果已经积累了足够内容，就切一刀
    - 普通文本行超出 max_chars 时切块
    - 图片行不单独触发切块，避免图片和描述文字被拆散
    """
    lines = [line.rstrip() for line in text.splitlines()]

    chunks: list[str] = []
    current: list[str] = []

    def current_len() -> int:
        return len("\n".join(current))

    def flush() -> None:
        chunk = "\n".join(current).strip()
        current.clear()
        if chunk:
            chunks.append(chunk)

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            if current:
                current.append("")
            continue

        if is_reference_line(line):
            continue

        if len(line) > max_chars:
            flush()
            chunks.extend(
                line[i:i + max_chars] for i in range(0, len(line), max_chars)
            )
            continue

        image_line = _is_image_line(line)

        if _is_heading(line) and current and current_len() >= max_chars // 2:
            flush()

        if not image_line and current and current_len() + len(line) + 1 > max_chars:
            flush()

        current.append(line)

    flush()

    # 过短的块尽量并入上一块，减少碎片
    merged: list[str] = []
    for chunk in chunks:
        if (
            merged
            and len(chunk) < min_chars
            and len(merged[-1]) + len(chunk) + 1 <= int(max_chars * 1.5)
        ):
            merged[-1] = merged[-1] + "\n" + chunk
        else:
            merged.append(chunk)

    return merged
