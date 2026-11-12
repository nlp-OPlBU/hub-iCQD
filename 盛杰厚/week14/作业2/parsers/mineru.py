"""MinerU 解析适配器。

对接 MinerU 官方 HTTP 服务（``pip install -U "mineru[core]"`` 后 ``mineru-api`` 启动的就是这个）：

* 同步接口  ``POST {MINERU_BASE_URL}/file_parse``  —— multipart 上传 PDF，一次性返回结果
* 异步接口  ``POST {MINERU_BASE_URL}/tasks`` + 轮询 ``GET /tasks/{id}/result``
            —— 大文件不会把 HTTP 连接挂十几分钟，而且能拿到 **真实进度百分比**，
            正好喂给功能 2 的「解析中」进度条

``MINERU_MODE=auto`` 时先试异步，服务端没有 /tasks 就退回同步。

两种模式的响应结构一致，都走同一个 `_build_parsed_doc()`：

    {"results": {"<文件名去后缀>": {
         "md_content":   "...markdown...",
         "content_list": [{"type":"text","text":...,"page_idx":0},
                          {"type":"image","img_path":"images/xxx.jpg","image_caption":[...]},
                          {"type":"table","table_body":"<table>...</table>"},
                          {"type":"equation","text":"$...$"}],
         "images":       {"images/xxx.jpg": "data:image/jpeg;base64,..."}}}}

产出的是 parsers.common.ParsedDoc，与内置兜底解析器完全同构。
"""
from __future__ import annotations

import base64
import json
import re
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import requests

import config
from parsers.common import (
    KIND_IMAGE,
    KIND_TABLE,
    KIND_TEXT,
    Block,
    ImageAsset,
    ParsedDoc,
    clean_text,
)

# 兜底：万一 .env 没写 MINERU_MODE
MINERU_MODE = (getattr(config, "MINERU_MODE", "") or "auto").lower()
ASYNC_POLL_INTERVAL = float(getattr(config, "MINERU_POLL_INTERVAL", 1.5) or 1.5)

_available_cache: Dict[str, Any] = {"ts": 0.0, "ok": False, "detail": ""}


class MinerUError(RuntimeError):
    """MinerU 不可用或解析失败。"""


# ---------------------------------------------------------------------------
# 可用性探测
# ---------------------------------------------------------------------------
def probe(timeout: float = 3.0, force: bool = False) -> Dict[str, Any]:
    """探测 MinerU 服务是否在跑（结果缓存 20s，避免每次上传都打一遍）。"""
    if force:
        _available_cache["ts"] = 0.0
    now = time.time()
    if now - _available_cache["ts"] < 20:
        return dict(_available_cache)
    url = f"{config.MINERU_BASE_URL}/health"
    ok, detail = False, ""
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code < 500:
            ok = True
            try:
                detail = json.dumps(resp.json(), ensure_ascii=False)[:200]
            except ValueError:
                detail = f"HTTP {resp.status_code}"
        else:
            detail = f"HTTP {resp.status_code}: {resp.text[:160]}"
    except Exception as exc:  # noqa: BLE001
        detail = f"{type(exc).__name__}: {exc}"
    _available_cache.update({"ts": now, "ok": ok, "detail": detail})
    return dict(_available_cache)


def available(force: bool = False) -> bool:
    if force:
        _available_cache.update({"ts": 0.0})
    return bool(probe().get("ok"))


def _headers() -> Dict[str, str]:
    return {"Authorization": f"Bearer {config.MINERU_TOKEN}"} if config.MINERU_TOKEN else {}


def _params() -> Dict[str, Any]:
    """解析参数。formula/table 打开后 markdown 里会带 LaTeX 与表格，问答效果更好。"""
    params: Dict[str, Any] = {
        "backend": config.MINERU_BACKEND,
        "parse_method": config.MINERU_PARSE_METHOD,
        "formula_enable": "true",
        "table_enable": "true",
        "return_md": "true",
        "return_content_list": "true",
        "return_images": "true",
        "start_page_id": 0,
    }
    if config.MINERU_MAX_PAGES:
        params["end_page_id"] = max(0, config.MINERU_MAX_PAGES - 1)
    return params


# ---------------------------------------------------------------------------
# 表格 HTML -> markdown
# ---------------------------------------------------------------------------
class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: List[List[str]] = []
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._span_cols = 1

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th"):
            self._row = self._row or []
            self._cell = []
            d = dict(attrs)
            try:
                self._span_cols = max(1, int(d.get("colspan") or 1))
            except ValueError:
                self._span_cols = 1
        elif tag == "br":
            if self._cell is not None:
                self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            text = clean_text("".join(self._cell)).replace("\n", " ").replace("|", "／")
            self._row.append(text)
            for _ in range(self._span_cols - 1):
                self._row.append("")
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def table_html_to_markdown(html: str) -> str:
    """MinerU 的 table_body 是 HTML，转 markdown 表格更适合喂给 LLM。"""
    if not html:
        return ""
    try:
        p = _TableParser()
        p.feed(html)
        rows = p.rows
    except Exception:  # noqa: BLE001
        rows = []
    if not rows:
        text = re.sub(r"<[^>]+>", " ", html)
        return clean_text(text)
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    head = rows[0]
    if not any(h.strip() for h in head):  # 没有表头就补一个
        head = [f"列{i + 1}" for i in range(width)]
        body = rows
    else:
        body = rows[1:]
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join([" --- "] * width) + "|"]
    for r in body:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 图片落盘
# ---------------------------------------------------------------------------
def _save_image(data: str, ref: str, doc_id: str) -> Tuple[Optional[str], int, int]:
    """把 base64/data-uri 图片写到 data/images/{doc_id}/，返回 (路径, 宽, 高)。"""
    from PIL import Image
    from io import BytesIO

    if not data:
        return None, 0, 0
    raw = data
    if isinstance(raw, bytes):
        payload = raw
    else:
        if raw.startswith("data:") or ";base64" in raw[:64]:
            _, _, b64 = raw.partition(",")
            payload = base64.b64decode(b64 or "")
        else:
            payload = base64.b64decode(raw)
    if not payload:
        return None, 0, 0
    safe = re.sub(r"[^0-A-Za-z_.-]+", "_", Path(str(ref)).name) or "img.jpg"
    out_dir = Path(config.IMAGE_DIR) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / safe
    try:
        img = Image.open(BytesIO(payload))
        w, h = img.size
        # 统一转成 PNG/JPEG 存盘，避免 PDF 内嵌的 CMYK / 带透明通道图在浏览器里显示异常
        if img.mode in ("P", "CMYK", "RGBA", "LA", "I;16"):
            conv = "RGBA" if "A" in img.mode or img.mode == "P" else "RGB"
            img = img.convert(conv)
            if conv == "RGB":
                out = out.with_suffix(".jpg")
                img.save(out, "JPEG", quality=90)
            else:
                out = out.with_suffix(".png")
                img.save(out, "PNG")
        else:
            img.save(out)
        return str(out), int(w), int(h)
    except Exception:  # noqa: BLE001
        # PIL 打不开（例如 EMF/WMF 矢量图）时原样落盘，至少文件还在
        try:
            out.write_bytes(payload)
            return str(out), 0, 0
        except OSError:
            return None, 0, 0


def _iter_image_entries(images: Any) -> List[Tuple[str, str]]:
    """MinerU 的 images 字段兼容 dict / list 两种形态。"""
    out: List[Tuple[str, str]] = []
    if isinstance(images, dict):
        for k, v in images.items():
            if isinstance(v, str):
                out.append((str(k), v))
            elif isinstance(v, dict):
                b64 = v.get("base64") or v.get("b64") or v.get("data") or ""
                name = v.get("name") or v.get("path") or k
                if b64:
                    out.append((str(name), str(b64)))
    elif isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                name = item.get("name") or item.get("img_path") or item.get("path") or ""
                b64 = item.get("base64") or item.get("data") or ""
                if name and b64:
                    out.append((str(name), str(b64)))
    return out


def _as_list(value: Any) -> List[str]:
    """MinerU 的 caption 字段有时是 list[str]，有时是 str。"""
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x]
    return [str(value)]


# ---------------------------------------------------------------------------
# content_list -> ParsedDoc
# ---------------------------------------------------------------------------
def _build_parsed_doc(
    result: Dict[str, Any],
    doc_id: str,
    filename: str,
    images_field: Any = None,
    meta: Optional[Dict[str, Any]] = None,
) -> ParsedDoc:
    content_list = result.get("content_list")
    if isinstance(content_list, str):
        try:
            content_list = json.loads(content_list)
        except json.JSONDecodeError:
            content_list = None
    if not isinstance(content_list, list):
        content_list = None

    doc = ParsedDoc(
        doc_id=doc_id,
        filename=filename,
        parser="mineru",
        pages=0,
        markdown=result.get("md_content") or "",
        meta={"mineru": meta or {}, "source": "content_list" if content_list else "md_content"},
    )

    # ---- 1) 图片先落盘，建立 ref -> 路径 的映射 ----
    saved: Dict[str, Tuple[str, int, int]] = {}
    entries = _iter_image_entries(images_field if images_field is not None else result.get("images"))
    for ref, data in entries:
        path, w, h = _save_image(data, ref, doc_id)
        if path:
            saved[_norm_ref(ref)] = (path, w, h)
    # content_list 里的 img_path 常见形态："images/xxx.jpg" / "./images/xxx.jpg" / "xxx.jpg"
    for item in content_list or []:
        if isinstance(item, dict) and item.get("img_path") and _norm_ref(item["img_path"]) not in saved:
            path = Path(str(config.IMAGE_DIR)) / doc_id / Path(str(item["img_path"])).name
            if path.exists():
                saved[_norm_ref(item["img_path"])] = (str(path), 0, 0)

    # ---- 2) 遍历 content_list 生成 blocks ----
    order = 0
    max_page = 0
    if content_list:
        for item in content_list:
            if not isinstance(item, dict):
                continue
            page = int(item.get("page_idx") or item.get("page") or 0) + 1  # page_idx 是 0-based
            max_page = max(max_page, page)
            kind = (item.get("type") or "text").lower()
            bbox = item.get("bbox") or item.get("bbox_s")
            if kind in ("image", "figure"):
                ref = _norm_ref(item.get("img_path") or item.get("image_path") or "")
                captions = _as_list(item.get("image_caption") or item.get("img_caption"))
                footnotes = _as_list(item.get("image_footnote"))
                caption = clean_text("；".join(captions + footnotes))
                path_w_h = saved.get(ref) or saved.get(Path(ref).name) or saved.get(Path(ref).stem)
                if path_w_h:
                    path, w, h = path_w_h
                    doc.images.append(ImageAsset(
                        ref=ref, page=page, path=path, caption=caption,
                        width=w, height=h, source="mineru",
                    ))
                    order += 1
                    doc.blocks.append(Block(KIND_IMAGE, page, caption, ref, bbox, order))
                else:
                    # 图没拿到（例如服务端没开 return_images）：保留题注文本，至少内容不丢
                    if caption:
                        order += 1
                        doc.blocks.append(Block(KIND_TEXT, page, f"[图片缺失] {caption}", "", bbox, order))
            elif kind == "table":
                body = item.get("table_body") or item.get("table_html") or ""
                md = table_html_to_markdown(body)
                cap = clean_text("；".join(_as_list(item.get("table_caption"))
                                          + _as_list(item.get("table_footnote"))))
                text = (f"{cap}\n{md}".strip() if cap else md) or clean_text(
                    item.get("text") or body)
                if text:
                    order += 1
                    doc.blocks.append(Block(KIND_TABLE, page, text, "", bbox, order))
            elif kind in ("equation", "interline_equation", "inline_equation"):
                text = clean_text(str(item.get("text") or item.get("latex") or ""))
                if text:
                    order += 1
                    doc.blocks.append(Block(KIND_TEXT, page, f"$${text}$$" if "$" not in text else text,
                                            "", bbox, order))
            elif kind == "list":
                items = _as_list(item.get("list_items"))
                text = "\n".join(f"- {i}" for i in items)
                if text:
                    order += 1
                    doc.blocks.append(Block(KIND_TEXT, page, text, "", bbox, order))
            else:  # text / title / code / misc
                text = clean_text(str(item.get("text") or ""))
                if not text:
                    continue
                level = item.get("text_level")
                if level:  # 标题块加 markdown 井号，方便 LLM 感知层级
                    text = "#" * (int(level) + 1) + f" {text.lstrip('#').strip()}"
                order += 1
                doc.blocks.append(Block(KIND_TEXT, page, text, "", bbox, order))
    doc.pages = max_page or int((meta or {}).get("pages") or 0)

    # ---- 3) content_list 缺失时的兜底：从 md_content 里粗切 ----
    if not doc.blocks and doc.markdown:
        doc.blocks, doc.images, doc.pages = _from_markdown(
            doc.markdown, saved, doc_id, doc.images, doc.pages
        )
    return doc


def _norm_ref(ref: Any) -> str:
    return str(ref or "").replace("\\", "/").lstrip("./").strip()


def _from_markdown(md: str, saved: Dict[str, Tuple[str, int, int]],
                   images: List[ImageAsset], pages: int) -> Tuple[List[Block], List[ImageAsset], int]:
    """老版本 MinerU 只给 markdown 时的退化路径：按标题/图片行切块。"""
    blocks: List[Block] = []
    order = 0
    buf: List[str] = []

    def flush() -> None:
        nonlocal order
        text = clean_text("\n".join(buf))
        buf.clear()
        if text:
            order += 1
            blocks.append(Block(KIND_TEXT, 0, text, "", None, order))

    for raw in md.splitlines():
        line = raw.rstrip()
        img = re.match(r"^!\[(.*?)\]\((.*?)\)\s*$", line.strip())
        if img:
            flush()
            alt, ref = img.group(1), _norm_ref(img.group(2))
            hit = saved.get(ref) or saved.get(Path(ref).name)
            order += 1
            if hit:
                path, w, h = hit
                if not any(i.ref == ref for i in images):
                    images.append(ImageAsset(ref=ref, page=0, path=path, caption=alt,
                                             width=w, height=h, source="mineru"))
                blocks.append(Block(KIND_IMAGE, 0, alt, ref, None, order))
            else:
                blocks.append(Block(KIND_TEXT, 0, f"[图片] {alt} ({ref})", "", None, order))
        elif line.startswith("|") and line.count("|") >= 2:
            buf.append(line)
        elif not line.strip():
            flush()
        else:
            if line.startswith("#"):
                flush()
                order += 1
                blocks.append(Block(KIND_TEXT, 0, line.strip(), "", None, order))
            else:
                buf.append(line)
    flush()
    if not any(b.kind == KIND_IMAGE for b in blocks):
        # md 里没有图但 images 目录有文件：按顺序补上，保证图片仍可被检索
        for im in images:
            order += 1
            blocks.append(Block(KIND_IMAGE, 0, im.caption, im.ref, None, order))
    return blocks, images, pages


# ---------------------------------------------------------------------------
# 对外：解析入口
# ---------------------------------------------------------------------------
def parse(
    pdf_path: str,
    doc_id: str,
    filename: str = "",
    progress_cb: Optional[Callable[[float, str], None]] = None,
) -> ParsedDoc:
    """解析一个 PDF。``progress_cb(percent_0_100, message)`` 用于喂「解析中」进度条。"""
    filename = filename or Path(pdf_path).name
    mode = MINERU_MODE or "auto"
    if mode == "auto":
        mode = "async" if _has_tasks_endpoint() else "sync"
    if mode == "async":
        try:
            return _parse_async(pdf_path, doc_id, filename, progress_cb)
        except MinerUError as exc:
            if "404" in str(exc) or "405" in str(exc) or "not found" in str(exc).lower():
                if progress_cb:
                    progress_cb(6, "服务端不支持异步接口，改用同步 /file_parse")
                return _parse_sync(pdf_path, doc_id, filename, progress_cb)
            raise
    return _parse_sync(pdf_path, doc_id, filename, progress_cb)


def _has_tasks_endpoint(timeout: float = 3.0) -> bool:
    try:
        resp = requests.get(f"{config.MINERU_BASE_URL}/tasks", timeout=timeout, headers=_headers())
        return resp.status_code < 400
    except Exception:  # noqa: BLE001
        return False


def _report(progress_cb: Optional[Callable[[float, str], None]], pct: float, msg: str) -> None:
    if progress_cb:
        try:
            progress_cb(pct, msg)
        except Exception:  # noqa: BLE001
            pass


def _parse_sync(pdf_path: str, doc_id: str, filename: str,
                progress_cb: Optional[Callable[[float, str], None]]) -> ParsedDoc:
    url = f"{config.MINERU_BASE_URL}{config.MINERU_API_PATH}"
    _report(progress_cb, 5, f"上传给 MinerU（{config.MINERU_BACKEND} 后端）")
    with open(pdf_path, "rb") as fh:
        files = {"files": (filename, fh.read(), "application/pdf")}
        try:
            resp = requests.post(url, files=files, data=_params(), headers=_headers(),
                                 timeout=(15, config.MINERU_TIMEOUT))
        except requests.RequestException as exc:
            raise MinerUError(f"MinerU 服务不可达（{url}）：{type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise MinerUError(f"MinerU 返回 HTTP {resp.status_code}: {resp.text[:300]}")
    _report(progress_cb, 30, "MinerU 解析完成，读取结果中")
    try:
        payload = resp.json()
    except ValueError as exc:
        raise MinerUError(f"MinerU 响应不是 JSON：{resp.text[:200]}") from exc
    return _doc_from_payload(payload, doc_id, filename, progress_cb)


def _parse_async(pdf_path: str, doc_id: str, filename: str,
                 progress_cb: Optional[Callable[[float, str], None]]) -> ParsedDoc:
    base = config.MINERU_BASE_URL
    _report(progress_cb, 5, "提交 MinerU 异步解析任务")
    with open(pdf_path, "rb") as fh:
        files = {"files": (filename, fh.read(), "application/pdf")}
        try:
            resp = requests.post(f"{base}/tasks", files=files, data=_params(),
                                 headers=_headers(), timeout=(15, 120))
        except requests.RequestException as exc:
            raise MinerUError(f"MinerU 服务不可达（{base}/tasks）：{exc}") from exc
    if resp.status_code not in (200, 201, 202):
        raise MinerUError(f"MinerU 提交任务返回 HTTP {resp.status_code}: {resp.text[:200]}")
    task = resp.json()
    task_id = task.get("task_id") or task.get("id") or task.get("task_uuid")
    if not task_id:
        raise MinerUError(f"MinerU 未返回 task_id：{json.dumps(task, ensure_ascii=False)[:200]}")

    deadline = time.time() + config.MINERU_TIMEOUT
    last_status = ""
    while time.time() < deadline:
        try:
            st = requests.get(f"{base}/tasks/{task_id}/status", headers=_headers(),
                              timeout=(10, 30)).json()
        except Exception as exc:  # noqa: BLE001
            raise MinerUError(f"查询 MinerU 任务状态失败：{exc}") from exc
        status = str(st.get("status") or "").lower()
        err = st.get("error")
        if err:
            raise MinerUError(f"MinerU 任务失败：{json.dumps(err, ensure_ascii=False)[:300]}")
        # 状态里带进度就按进度报；没带就在 5~35 之间缓慢推进，页面至少看得出「还在跑」
        pct = st.get("progress")
        try:
            pct = float(pct) if pct is not None else None
        except (TypeError, ValueError):
            pct = None
        if pct is None:
            elapsed = min(1.0, (config.MINERU_TIMEOUT - (deadline - time.time())) / max(config.MINERU_TIMEOUT, 1))
            pct = 5 + 30 * elapsed
        else:
            if pct <= 1.0:
                pct *= 100
            pct = min(35.0, 5 + 30 * (pct / 100.0))
        if status != last_status:
            _report(progress_cb, pct, f"MinerU 任务 {status or 'running'}")
            last_status = status
        else:
            _report(progress_cb, pct, f"MinerU 解析中 {int(pct)}%")
        if status in ("completed", "done", "success", "finished"):
            break
        if status in ("failed", "error"):
            raise MinerUError(f"MinerU 任务状态 {status}")
        time.sleep(ASYNC_POLL_INTERVAL)
    else:
        raise MinerUError(f"MinerU 任务超时（{config.MINERU_TIMEOUT}s）")

    _report(progress_cb, 34, "拉取 MinerU 结果")
    res = requests.get(f"{base}/tasks/{task_id}/result", params={"kind": "all"},
                       headers=_headers(), timeout=(15, 120))
    if res.status_code != 200:
        raise MinerUError(f"MinerU 结果接口 HTTP {res.status_code}: {res.text[:200]}")
    return _doc_from_payload(res.json(), doc_id, filename, progress_cb)


def _doc_from_payload(payload: Dict[str, Any], doc_id: str, filename: str,
                      progress_cb: Optional[Callable[[float, str], None]]) -> ParsedDoc:
    results = payload.get("results") or {}
    if not isinstance(results, dict) or not results:
        raise MinerUError(f"MinerU 响应里没有 results 字段：{json.dumps(payload, ensure_ascii=False)[:300]}")
    key = next(iter(results))
    # 多文件响应里挑文件名最接近的那个
    for k in results:
        if Path(k).stem and Path(k).stem in (Path(filename).stem or k):
            key = k
            break
    result = results[key] or {}
    if isinstance(result.get("status"), str) and result["status"].lower() in ("failed", "error"):
        raise MinerUError(f"MinerU 解析失败：{json.dumps(result, ensure_ascii=False)[:300]}")
    doc = _build_parsed_doc(result, doc_id, filename, result.get("images"),
                            {"version": payload.get("version"), "backend": payload.get("backend")})
    _report(progress_cb, 38, f"解析完成：{len(doc.blocks)} 块 / {len(doc.images)} 图")
    return doc
