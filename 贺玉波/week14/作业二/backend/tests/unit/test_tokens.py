"""token 估算：只保证量级正确，用于决定历史能装多少。"""

from __future__ import annotations

from app.core.tokens import estimate_tokens


def test_empty_text_is_zero() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0


def test_cjk_counts_one_per_char() -> None:
    assert estimate_tokens("多模态检索") == 5


def test_latin_counts_four_chars_per_token() -> None:
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("abcde") == 1


def test_mixed_text() -> None:
    # 2 个汉字 + 4 个非汉字字符（RAG + 空格）
    assert estimate_tokens("RAG 检索") == 3
