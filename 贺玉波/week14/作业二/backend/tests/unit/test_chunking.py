"""切块规则：短文本单块、长文本切分、重叠、序号连续。"""

from __future__ import annotations

from app.pipeline.chunking import split_markdown


def test_short_text_single_chunk() -> None:
    chunks = split_markdown("第一段。\n\n第二段。", max_chars=100, overlap=10)
    assert len(chunks) == 1
    assert chunks[0].idx == 0
    assert "第一段。" in chunks[0].text and "第二段。" in chunks[0].text


def test_paragraphs_merged_within_limit() -> None:
    text = "\n\n".join(f"第{i}段内容。" for i in range(20))
    chunks = split_markdown(text, max_chars=50, overlap=0)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 50 for chunk in chunks)
    assert [chunk.idx for chunk in chunks] == list(range(len(chunks)))


def test_overlap_keeps_boundary_context() -> None:
    text = "\n\n".join("甲" * 40 for _ in range(4))
    chunks = split_markdown(text, max_chars=100, overlap=30)
    assert len(chunks) >= 2
    # 后一块开头必须包含前一块结尾，避免答案被边界割裂
    assert chunks[1].text.startswith(chunks[0].text[-30:])


def test_long_sentence_hard_split() -> None:
    chunks = split_markdown("字" * 250, max_chars=100, overlap=0)
    assert [len(chunk.text) for chunk in chunks] == [100, 100, 50]


def test_kind_detection() -> None:
    chunks = split_markdown("# 标题\n\n正文段落。", max_chars=100, overlap=0)
    kinds = [chunk.kind for chunk in chunks]
    assert kinds[0] == "heading"


def test_empty_text() -> None:
    assert split_markdown("   \n\n  ", max_chars=100, overlap=10) == []


def test_page_no_propagated_for_citation() -> None:
    chunks = split_markdown("第一页内容" * 10, max_chars=20, overlap=0, page_no=7)
    assert chunks
    assert all(chunk.page_no == 7 for chunk in chunks)

    # 不传页码时保持 None，交由上层决定兜底
    assert split_markdown("正文", max_chars=100, overlap=0)[0].page_no is None
