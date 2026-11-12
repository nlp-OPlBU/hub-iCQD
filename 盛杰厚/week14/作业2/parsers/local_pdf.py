"""内置兜底解析器（不依赖 MinerU）。

用 ``pdfplumber`` 读文本行 / 表格 / 图片框，用 ``pypdfium2`` 把图片框所在位置**渲染成 PNG**
（比直接抠 XObject 更好：矢量图、被 SMask 弄花的图都能拿到，且天然带上题注位置）。

产出与 MinerU 完全同构的 ParsedDoc：

    文本块   按「同一栏 + 行间距小 + 句末不是标点」合并成段落，字号明显偏大的当标题
    表格块   pdfplumber 的表格识别结果 → markdown 表格
    图片块   同页相邻图片框合并（科技文献里一张图常被切成多个 XObject），去重（页眉 logo 只留一份）
             再就近找「图 x / Fig. x / 表 x」开头的行当题注

为什么保留它
------------
1. MinerU 完整安装要拉 torch + 模型（GB 级），答辩/演示环境下不一定装得起来；
2. 有些 PDF（纯文字、无扫描）用内置解析器反而更快；
3. 有了同构的兜底实现，Kafka 链路 / 向量库 / 问答 这些**真正要验证的部分**才不被解析器卡住。

生产建议：装 MinerU（``pip install -U "mineru[core]" && mineru-api``），扫描件/复杂版式
的表格与公式还原质量明显更好。``MINERU_STRICT=1`` 可以让「MinerU 不可用」直接判解析失败，
而不是悄悄退回兜底。
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from parsers.common import (
    KIND_IMAGE,
    KIND_TABLE,
    KIND_TEXT,
    Block,
    ImageAsset,
    ParsedDoc,
    clean_text,
    join_two_lines,
    looks_like_caption,
)

# ---- 可调参数（环境变量覆盖）----
import config  # noqa: E402  放在末尾以免上面 dataclass 依赖它

FIGURE_DPI = int(getattr(config, "LOCAL_FIGURE_DPI", 0) or 150)
MIN_FIGURE_EDGE = float(getattr(config, "LOCAL_MIN_FIGURE_EDGE", 0) or 55)   # pt
MIN_FIGURE_AREA = float(getattr(config, "LOCAL_MIN_FIGURE_AREA", 0) or 3000)  # pt^2
MAX_FIGURES_PER_DOC = int(getattr(config, "LOCAL_MAX_FIGURES", 0) or 200)
TABLES_ENABLED = str(getattr(config, "LOCAL_TABLES", "1") or "1").lower() not in ("0", "false", "no")
MAX_PAGES = int(getattr(config, "LOCAL_MAX_PAGES", 0) or 0)


class LocalParseError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def page_count(pdf_path: str) -> int:
    try:
        import pdfplumber

        with pdfplumber.open(pdf_path) as pdf:
            return len(pdf.pages)
    except Exception:  # noqa: BLE001
        try:
            import pypdfium2 as pdfium

            with pdfium.PdfDocument(pdf_path) as doc:
                return len(doc)
        except Exception as exc:  # noqa: BLE001
            raise LocalParseError(f"无法读取 PDF：{exc}") from exc


def _rects_overlap_x(a: Sequence[float], b: Sequence[float], tol: float = 8.0) -> float:
    """x 方向重叠长度占较窄者宽度的比例。"""
    lo = max(a[0], b[0])
    hi = min(a[2], b[2])
    if hi <= lo:
        return 0.0
    narrow = min(a[2] - a[0], b[2] - b[0])
    return (hi - lo) / narrow if narrow > 0 else 0.0


def merge_boxes(boxes: List[List[float]], gap: float = 10.0) -> List[List[float]]:
    """把同一页里紧邻的图片框合并成整图区域（论文里一张示意图常被切成若干块）。"""
    if not boxes:
        return []
    boxes = sorted(boxes, key=lambda b: (b[1], b[0]))
    merged: List[List[float]] = [boxes[0][:]]
    changed = True
    while changed:  # 反复合并直到不再变化（一次遍历会漏掉链式相邻）
        changed = False
        out: List[List[float]] = []
        for b in merged:
            hit = None
            for o in out:
                vert_gap = max(o[1], b[1]) - min(o[3], b[3])
                if _rects_overlap_x(o, b) > 0.45 and vert_gap < gap:
                    hit = o
                    break
            if hit is None:
                out.append(b[:])
            else:
                hit[0] = min(hit[0], b[0])
                hit[1] = min(hit[1], b[1])
                hit[2] = max(hit[2], b[2])
                hit[3] = max(hit[3], b[3])
                changed = True
        merged = out
    return merged


def _line_metrics(line: Dict[str, Any]) -> Tuple[float, float, float, float, float]:
    """(x0, x1, top, bottom, font_size) —— 各 pdfplumber 版本字段名略有差异，做兼容。"""
    x0 = float(line.get("x0") or 0.0)
    x1 = float(line.get("x1") or x0 + 10)
    top = float(line.get("top") or 0.0)
    bottom = float(line.get("bottom") or line.get("doctop") or top)
    size = line.get("size")
    if not size:
        chars = line.get("chars") or []
        size = chars[0].get("size", 10.0) if chars else 10.0
    if not size:
        size = max(1.0, bottom - top)
    return x0, x1, top, bottom, float(size)


def _inside(bbox: Sequence[float], regions: Sequence[Sequence[float]], tol: float = 2.0) -> bool:
    x0, y0, x1, y1 = bbox
    for r in regions:
        if x0 >= r[0] - tol and x1 <= r[2] + tol and y0 >= r[1] - tol and y1 <= r[3] + tol:
            return True
    return False


_SENT_END = re.compile(r"[。！？!?；;：:.…\"'”’)）\]]\s*$")
_HEADING = re.compile(r"^(第[一二三四五六七八九十百]+[章节部分条]|[0-9]+(\.[0-9]+){0,3}\s+\S"
                      r"|[A-G][\.、]\s+\S|[（(][一二三四五六七八九十]+[)）])")


# ---------------------------------------------------------------------------
# 段落聚合
# ---------------------------------------------------------------------------
def group_lines_into_blocks(
    lines: List[Dict[str, Any]], skip_regions: Sequence[Sequence[float]] = []
) -> List[Dict[str, Any]]:
    """返回 [{"idx","text","top","bottom"}]，每个元素是一个段落/标题。

    ``idx`` 用于「题注已被某张图消费」的标记，避免同一句话既当题注又当正文块重复入库。
    """
    items = []
    for ln in lines:
        text = (ln.get("text") or "").strip()
        if not text:
            continue
        x0, x1, top, bottom, size = _line_metrics(ln)
        if _inside((x0, top, x1, bottom), skip_regions):
            continue
        items.append((text, x0, x1, top, bottom, size))
    if not items:
        return []

    sizes = sorted(s for *_, s in items)
    median_size = sizes[len(sizes) // 2]

    # 每段收集 (text, x0, x1, top, bottom, size)，最后按几何信息决定行间怎么拼
    blocks: List[List[Any]] = []  # [ [line, ...], top, bottom, max_size ]
    cur: Optional[List[Any]] = None
    prev: Optional[Tuple[str, float, float, float, float, float]] = None
    for it in items:
        text, x0, x1, top, bottom, size = it
        is_heading = bool(_HEADING.match(text)) or size > median_size * 1.18 or len(text) <= 28 and size >= median_size * 1.1
        starts_new = False
        if prev is not None:
            gap = top - prev[4]
            line_h = max(1.0, prev[4] - prev[3])
            big_gap = gap > line_h * 0.62
            indent = x0 - prev[1] > 12
            prev_no_punct = not _SENT_END.search(prev[0])
            # 断行续排：小间距 + 没有明显缩进 + 上一行不是句号结尾
            starts_new = big_gap or indent or is_heading or (not prev_no_punct and big_gap)
        if cur is None or starts_new:
            cur = [[it], top, bottom, size]
            blocks.append(cur)
        else:
            cur[0].append(it)
            cur[2] = bottom
            cur[3] = max(cur[3], size)
        prev = it

    out: List[Dict[str, Any]] = []
    for lines_, top, bottom, size in blocks:
        out.append({"idx": len(out), "text": _join_lines(lines_), "top": top, "bottom": bottom})
    return out


def _join_lines(lines_: List[Tuple[str, float, float, float, float, float]]) -> str:
    """把一段之内的若干行拼成连续文本。

    ``clean_text`` 只看字符、看不到版式，这里用几何信息补上关键判据：
    上一行**几乎排到本段右边界**（说明是在空格处折行）→ 用空格拼；
    上一行没排满就换行了（说明是在单词内部断开，例如 ``mmrag.d / oc.status``）→ 不加空格拼。
    中文两侧一律不加空格。
    """
    if not lines_:
        return ""
    max_x1 = max(l[2] for l in lines_)
    tol = max(6.0, 0.04 * max_x1)
    buf = (lines_[0][0] or "").strip()
    for i in range(1, len(lines_)):
        nxt = (lines_[i][0] or "").strip()
        if not nxt:
            continue
        # 上一行排满到右边距 = 在空格处折行；没排满 = 单词内部被切断
        filled = (max_x1 - lines_[i - 1][2]) <= tol
        buf = join_two_lines(buf, nxt, filled)
    return buf


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------
def parse(
    pdf_path: str,
    doc_id: str,
    filename: str = "",
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> ParsedDoc:
    filename = filename or Path(pdf_path).name
    try:
        import pdfplumber
        import pypdfium2 as pdfium
        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        raise LocalParseError(
            f"内置解析器缺少依赖（pdfplumber/pypdfium2/Pillow）：{exc}。"
            "请 pip install -r requirements.txt，或改用 MinerU。"
        ) from exc

    doc = ParsedDoc(doc_id=doc_id, filename=filename, parser="pdf_local", meta={"source": "pdfplumber"})
    out_dir = Path(config.IMAGE_DIR) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)

    with pdfplumber.open(pdf_path) as pdf, pdfium.PdfDocument(pdf_path) as render_doc:
        total = len(pdf.pages)
        limit = min(total, MAX_PAGES) if MAX_PAGES else total
        doc.pages = total
        seen_hashes: Dict[str, int] = {}
        n_figures = 0
        order = 0

        for pidx in range(limit):
            page = pdf.pages[pidx]
            page_no = pidx + 1
            if progress_cb and (pidx % 3 == 0 or pidx == limit - 1):
                try:
                    # 内置解析器阶段占 10%~38% 区间
                    progress_cb(10 + 28 * (pidx + 1) / max(1, limit),
                                f"内置解析器：第 {page_no}/{total} 页")
                except Exception:  # noqa: BLE001
                    pass

            page_w = float(getattr(page, "width", 612) or 612)
            page_h = float(getattr(page, "height", 792) or 792)

            # ---------- 1) 表格区域 ----------
            table_regions: List[List[float]] = []
            table_blocks: List[Tuple[str, float, float]] = []
            if TABLES_ENABLED:
                try:
                    for t in page.find_tables():
                        bbox = list(t.bbox)  # x0, top, x1, bottom
                        rows = t.extract()
                        md = _rows_to_markdown(rows)
                        if md:
                            table_regions.append(bbox)
                            table_blocks.append((md, bbox[1], bbox[3]))
                except Exception:  # noqa: BLE001
                    table_regions, table_blocks = [], []

            # ---------- 2) 图片区域 ----------
            raw_boxes: List[List[float]] = []
            for im in (page.images or []):
                try:
                    x0 = float(im.get("x0", 0)); top = float(im.get("top", 0))
                    x1 = float(im.get("x1", 0)); bottom = float(im.get("bottom", 0))
                except (TypeError, ValueError, AttributeError):
                    continue
                w, h = x1 - x0, bottom - top
                if w < MIN_FIGURE_EDGE or h < MIN_FIGURE_EDGE:
                    continue
                if w * h < MIN_FIGURE_AREA:
                    continue
                if w > page_w * 0.98 and h > page_h * 0.98:
                    continue  # 整页背景图，跳过
                raw_boxes.append([x0, top, x1, bottom])
            fig_regions = merge_boxes(raw_boxes)

            # ---------- 3) 文本段落 ----------
            try:
                lines = page.extract_text_lines() or []
            except Exception:  # noqa: BLE001
                lines = [{"text": t, "top": i * 12, "bottom": i * 12 + 10,
                          "x0": 0, "x1": page_w} for i, t in enumerate((page.extract_text() or "").split("\n"))]
            paragraphs = group_lines_into_blocks(lines, skip_regions=table_regions + fig_regions)
            used_caps: set = set()   # 已被某张图当作题注的段落 idx

            # ---------- 4) 渲染并裁出图片 ----------
            figures: List[Tuple[str, float, float, str]] = []  # (ref, top, bottom, caption)
            if fig_regions and n_figures < MAX_FIGURES_PER_DOC:
                scale = FIGURE_DPI / 72.0
                page_img = None
                try:
                    bmp = render_doc[pidx].render(scale=scale)
                    page_img = bmp.to_pil()
                except Exception:  # noqa: BLE001
                    page_img = None
                for ri, (x0, top, x1, bottom) in enumerate(sorted(fig_regions, key=lambda b: b[1])):
                    if n_figures >= MAX_FIGURES_PER_DOC:
                        break
                    if page_img is None:
                        break
                    box = (max(0, int(x0 * scale)), max(0, int(top * scale)),
                           min(page_img.width, int(x1 * scale) + 1),
                           min(page_img.height, int(bottom * scale) + 1))
                    if box[2] - box[0] < 8 or box[3] - box[1] < 8:
                        continue
                    try:
                        crop = page_img.crop(box)
                    except Exception:  # noqa: BLE001
                        continue
                    digest = hashlib.sha1(crop.tobytes()).hexdigest()[:16]
                    if seen_hashes.get(digest, 0) >= 1 and (bottom < page_h * 0.12 or top > page_h * 0.9):
                        seen_hashes[digest] = seen_hashes.get(digest, 0) + 1
                        continue  # 页眉/页脚重复出现的 logo，丢
                    seen_hashes[digest] = seen_hashes.get(digest, 0) + 1
                    ref = f"p{page_no}_fig{ri + 1}.png"
                    path = out_dir / ref
                    try:
                        if crop.mode not in ("RGB", "RGBA", "L"):
                            crop = crop.convert("RGB")
                        crop.save(path, "PNG", optimize=True)
                    except Exception:  # noqa: BLE001
                        continue
                    n_figures += 1
                    caption, cidx = _find_caption(paragraphs, top, bottom)
                    if cidx is not None:
                        used_caps.add(cidx)   # 这句话已经是该图的题注，别再作为正文块输出
                    doc.images.append(ImageAsset(
                        ref=ref, page=page_no, path=str(path), caption=caption,
                        width=crop.width, height=crop.height, source="pdf_local",
                    ))
                    figures.append((ref, top, bottom, caption))

            # ---------- 5) 按纵向位置穿插输出，保持阅读顺序 ----------
            timeline: List[Tuple[float, int, Any]] = []
            for p in paragraphs:
                if p["idx"] in used_caps:
                    continue
                timeline.append((p["top"], 0, (p["text"], p["bottom"])))
            for ref, top, bottom, caption in figures:
                timeline.append((top, 1, (ref, caption, bottom)))
            for md, top, bottom in table_blocks:
                timeline.append((top, 2, (md, bottom)))
            timeline.sort(key=lambda t: (t[0], t[1]))

            for _top, kind, payload in timeline:
                if kind == 1:
                    ref, caption, _b = payload
                    order += 1
                    doc.blocks.append(Block(KIND_IMAGE, page_no, caption, ref, None, order))
                elif kind == 2:
                    md, _b = payload
                    order += 1
                    doc.blocks.append(Block(KIND_TABLE, page_no, md, "", None, order))
                else:
                    text, _b = payload
                    text = clean_text(text)
                    if not text:
                        continue
                    order += 1
                    doc.blocks.append(Block(KIND_TEXT, page_no, text, "", None, order))
    doc.meta["figures_captured"] = doc.n_images()
    doc.meta["pages_scanned"] = limit
    return doc


def _find_caption(paragraphs: List[Dict[str, Any]], fig_top: float,
                  fig_bottom: float) -> Tuple[str, Optional[int]]:
    """题注一般在图下方 0~40pt，偶尔在上方。就近找「图x/Fig.x」开头的行。

    返回 ``(题注, 段落idx)``。被当作题注的段落会被记下来、不再作为正文块输出，
    否则同一句「图 1 …」会既当图片描述又当正文，入库两份、检索时重复命中。
    """
    best, best_dist, best_idx = "", 1e9, None
    for p in paragraphs:
        text = p["text"]
        t = (text or "").strip().split("\n")[0]
        if not looks_like_caption(t):
            continue
        below = p["top"] - fig_bottom
        above = fig_top - p["bottom"]
        dist = below if -6 <= below <= 45 else (abs(above) + 200 if -6 <= above <= 25 else 1e9)
        if dist < best_dist:
            best_dist, best, best_idx = dist, clean_text(text).replace("\n", " "), p["idx"]
    return (best[:240] if best else ""), best_idx


def _rows_to_markdown(rows: List[List[Optional[str]]]) -> str:
    clean_rows: List[List[str]] = []
    for r in rows:
        cells = [("" if c is None else re.sub(r"\s+", " ", str(c))).strip() for c in (r or [])]
        if any(cells):
            clean_rows.append(cells)
    if not clean_rows:
        return ""
    width = max(len(r) for r in clean_rows)
    clean_rows = [r + [""] * (width - len(r)) for r in clean_rows]
    head = clean_rows[0]
    if not any(h.strip() for h in head):
        head = [f"列{i + 1}" for i in range(width)]
        body = clean_rows
    else:
        body = clean_rows[1:]
    lines = ["| " + " | ".join(h.replace("|", "／") or "-" for h in head) + " |",
             "|" + "|".join([" --- "] * width) + "|"]
    for r in body:
        lines.append("| " + " | ".join(c.replace("|", "／") or " " for c in r) + " |")
    return "\n".join(lines)


if __name__ == "__main__":  # 快速自检：python parsers/local_pdf.py some.pdf
    import json
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else ""
    if not target:
        print("用法: python parsers/local_pdf.py <pdf>")
        raise SystemExit(1)
    d = parse(target, doc_id="selftest", progress_cb=lambda p, m: print(f"{p:5.1f}% {m}"))
    print(json.dumps(d.stats(), ensure_ascii=False, indent=2))
    for b in d.blocks[:12]:
        print(f"[{b.kind}] p{b.page}: {(b.text or '')[:70]!r}")
