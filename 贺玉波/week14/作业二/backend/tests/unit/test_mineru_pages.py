"""MinerU content_list 解析：页码还原与异常兜底。"""

from __future__ import annotations

import json
from pathlib import Path

from app.pipeline.mineru import extract_images, load_pages, pages_from_markdown


def _write(tmp_path: Path, payload: object, name: str = "demo_content_list.json") -> Path:
    path = tmp_path / "auto" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_pages_grouped_and_one_based(tmp_path: Path) -> None:
    _write(
        tmp_path,
        [
            {"type": "text", "text": "封面标题", "page_idx": 0},
            {"type": "text", "text": "第一页正文", "page_idx": 0},
            {"type": "table", "table_body": "| a | b |", "page_idx": 1},
            {"type": "image", "img_path": "images/x.jpg", "page_idx": 2},
        ],
    )
    pages = load_pages(tmp_path)
    assert [page_no for page_no, _ in pages] == [1, 2]
    assert "封面标题" in pages[0][1] and "第一页正文" in pages[0][1]
    assert pages[1][1] == "| a | b |"


def test_missing_content_list_returns_empty(tmp_path: Path) -> None:
    assert load_pages(tmp_path) == []


def test_broken_json_falls_back(tmp_path: Path) -> None:
    path = tmp_path / "bad_content_list.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_pages(tmp_path) == []


def test_pages_from_markdown_splits_by_page_mark() -> None:
    markdown = (
        "<!-- page 1 of 3 -->\n\n# 封面\n\n正文一\n\n"
        "<!-- page 2 of 3 -->\n\n正文二\n\n"
        "<!-- page 3 of 3 -->\n\n"
    )
    pages = pages_from_markdown(markdown)
    assert [page_no for page_no, _ in pages] == [1, 2]
    assert pages[0][1] == "# 封面\n\n正文一"
    assert pages[1][1] == "正文二"


def test_pages_from_markdown_without_marks_returns_empty() -> None:
    assert pages_from_markdown("# 没有页码标记") == []


def _pdf_with_images(path: Path) -> None:
    """造一个 2 页 PDF：首页一张 100x100 图，次页一张 20x20 小图（应被过滤）。"""
    import pymupdf

    with pymupdf.open() as pdf:
        for side in (100, 20):
            pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, side, side), 0)
            pix.clear_with(200)
            page = pdf.new_page()
            page.insert_image(pymupdf.Rect(10, 10, 110, 110), stream=pix.tobytes("png"))
        pdf.save(str(path))


def test_extract_images_filters_small_and_names_by_page(tmp_path: Path) -> None:
    pdf_path = tmp_path / "demo.pdf"
    _pdf_with_images(pdf_path)
    images = extract_images(pdf_path, tmp_path / "images")
    assert [item.name for item in images] == ["p001_01.png"]
    assert images[0].stat().st_size > 0


def test_extract_images_missing_pdf_returns_empty(tmp_path: Path) -> None:
    assert extract_images(tmp_path / "nope.pdf", tmp_path / "images") == []
