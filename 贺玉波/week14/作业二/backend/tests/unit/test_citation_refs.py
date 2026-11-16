"""引用编号校验与提示词编号上界的单测。"""

from __future__ import annotations

from app.graph.qa_graph import (
    SYSTEM_PROMPT,
    build_messages,
    citation_numbers,
    invalid_refs,
    sanitize_refs,
)


def test_citation_numbers_extracts_every_ref() -> None:
    assert citation_numbers("结论见 [1] 与 [2][3]") == [1, 2, 3]
    assert citation_numbers("没有任何引用") == []


def test_invalid_refs_flags_out_of_range() -> None:
    assert invalid_refs("结论 [1][9]", 3) == [9]
    assert invalid_refs("结论 [1][2]", 3) == []
    # [0] 也不是合法编号
    assert invalid_refs("结论 [0]", 3) == [0]


def test_sanitize_refs_removes_only_out_of_range() -> None:
    assert sanitize_refs("结论 [1][9]。", 3) == "结论 [1]。"
    assert sanitize_refs("结论 [12]", 3) == "结论 "
    assert sanitize_refs("[1][2]", 2) == "[1][2]"


def test_build_messages_tells_model_the_ref_upper_bound() -> None:
    messages = build_messages("问题", "【资料】正文", [], max_ref=3)
    assert messages[1]["role"] == "system"
    assert "[1] 到 [3]" in messages[1]["content"]


def test_build_messages_skips_ref_bound_without_context() -> None:
    messages = build_messages("你好", "", [], has_context=False, max_ref=3)
    assert len(messages) == 2


def test_system_prompt_allows_general_knowledge_but_separates_it() -> None:
    """不能被资料锁死：允许补充通用知识，但必须与引用区分开、不许编造来源。"""
    assert "通用知识" in SYSTEM_PROMPT
    assert "非资料内容" in SYSTEM_PROMPT
    assert "只依据" not in SYSTEM_PROMPT


def test_no_material_note_is_injected() -> None:
    messages = build_messages("库里没有的问题", "", [], has_context=False, no_material=True)
    notes = [item["content"] for item in messages if item["role"] == "system"]
    assert any("不来自知识库" in text for text in notes)
    # 没资料时不能给出引用编号上界，否则等于暗示模型去编引用
    assert not any("[1] 到 [" in text for text in notes)
