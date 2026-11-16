"""token 估算：不引 tiktoken（多一份 2MB 词表，且中文分词与 Qwen 不完全一致）。

经验值：CJK 字符约 1 字 1 token，其余（英文、数字、标点）约 4 字符 1 token。
用途只有一个——决定历史消息装多少进 prompt，估高一点比估低安全。
"""

from __future__ import annotations

CJK_START = "\u4e00"
CJK_END = "\u9fff"


def estimate_tokens(text: str | None) -> int:
    if not text:
        return 0
    cjk = sum(1 for char in text if CJK_START <= char <= CJK_END)
    return cjk + max(0, (len(text) - cjk) // 4)
