"""Web 后端 —— FastAPI：多文件上传 → Kafka，解析状态 SSE，多模态问答（图文混排）。

接口一览
========
==== 方法 ====  ==== 路径 ====                        ==== 作用 ====
POST   /api/documents        上传多个 PDF，落盘后**投递 Kafka**（不在本进程解析）
GET    /api/documents        文档列表（含 state/progress，页面刷新用）
GET    /api/documents/{id}   单文档详情（分块 + 图片清单）
POST   /api/documents/{id}/reparse   重新投递解析
DELETE /api/documents/{id}   删除文档（连带向量、图片行）
GET    /api/events           **SSE**：转发 Kafka ``mmrag.doc.status``（待解析/解析中/解析完成）
POST   /api/qa               提问（可带图）→ 投递 ``mmrag.qa.request``；响应本身是 **SSE 流**
GET    /api/qa/history       会话历史
GET    /api/images/{id}      取解析抽出的图片（答案图文混排的 ``<img>`` 源）
GET    /api/search           只看检索结果（调阈值很有用）
GET    /api/health           后端真实状态：Kafka 后端、topic、消费组、MinerU、embedding、LLM
GET    /api/pipeline         最近的状态消息与总线指标（答辩演示链路时好用）

进程内 Worker
-------------
``WORKERS_INPROC=1``（默认）时，Web 进程启动就把 parse/index/qa 三个消费者拉起来，
一条命令即可演示；把它们拆成独立进程 ``python worker.py --roles parse`` 后，
把这里设成 0，**同一套代码不用改其他地方** —— 这正是 Kafka 解耦的意义。

SSE 转发链
----------
Kafka 消费线程 → ``loop.call_soon_threadsafe`` → 每个连接的 ``asyncio.Queue`` → SSE bytes
所以页面上的进度条不是前端假动画，是 Worker 真的在 Kafka 上发的消息。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import queue
import shutil
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import config
import messages
from bus import get_bus
from store import get_store

# FastAPI 相关导入放在函数外，报错信息更直白
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"}
MAX_QA_IMAGE_MB = 8


# ---------------------------------------------------------------------------
# 进程级状态
# ---------------------------------------------------------------------------
class AppState:
    def __init__(self) -> None:
        self.bus = None
        self.store = None
        self.workers: Dict[str, Any] = {}
        self.status_listeners: Set[asyncio.Queue] = set()
        self.qa_listeners: Dict[str, asyncio.Queue] = {}
        self.recent_status: List[Dict[str, Any]] = []      # 最近 N 条状态消息（/api/pipeline）
        self.started_at = time.time()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.subs: List[Any] = []


state = AppState()


def _remember_status(payload: Dict[str, Any]) -> None:
    state.recent_status.append(payload)
    if len(state.recent_status) > 200:
        state.recent_status = state.recent_status[-200:]


def _on_status_from_bus(payload: Dict[str, Any], key: Optional[str]) -> None:
    """Kafka 消费线程里的回调：落库 + 转发给所有 SSE 连接。"""
    if not isinstance(payload, dict) or payload.get("type") != "status":
        return
    try:
        state.store.apply_status(payload)
    except Exception as exc:  # noqa: BLE001
        print(f"[web] 状态落库失败: {exc}", flush=True)
    _remember_status(payload)
    loop = state.loop
    if loop is None or loop.is_closed():
        return
    for q in list(state.status_listeners):
        try:
            loop.call_soon_threadsafe(q.put_nowait, payload)
        except (RuntimeError, asyncio.QueueFull):
            pass


def _on_qa_from_bus(payload: Dict[str, Any], key: Optional[str]) -> None:
    """QA Worker 发来的消息，按 request_id 路由到对应连接。"""
    if not isinstance(payload, dict) or payload.get("type") != "qa_answer":
        return
    rid = payload.get("request_id")
    q = state.qa_listeners.get(rid or "")
    loop = state.loop
    if q is None or loop is None or loop.is_closed():
        return
    try:
        loop.call_soon_threadsafe(q.put_nowait, payload)
    except (RuntimeError, asyncio.QueueFull):
        pass


# ---------------------------------------------------------------------------
# 启动 / 关闭
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app):
    loop = asyncio.get_running_loop()
    state.loop = loop
    bus = get_bus()
    store = get_store()
    state.bus, state.store = bus, store
    bus.ensure_topics()

    # 先订阅，再起 Worker，保证第一条上传的状态不会丢
    state.subs.append(bus.subscribe(config.TOPIC_STATUS, _on_status_from_bus,
                                    group=f"{config.KAFKA_CLIENT_ID}-web-status",
                                    earliest=False, name="web-status"))
    state.subs.append(bus.subscribe(config.TOPIC_QA_ANS, _on_qa_from_bus,
                                    group=f"{config.KAFKA_CLIENT_ID}-web-qa",
                                    earliest=False, name="web-qa"))

    # 重启后把卡在“解析中”的文档标失败，页面不会永远转圈
    stale = store.recover_stale(older_than=60)
    if stale:
        print(f"[web] {stale} 个文档因服务重启中断，已标记为解析失败", flush=True)

    if config.WORKERS_INPROC:
        import worker

        state.workers = worker.start(["parse", "index", "qa"], bus, store)
        print("[web] 已在本进程启动 Worker 角色：parse / index / qa "
              "（要拆分进程见 README）", flush=True)

    # 预热 embedding：**同步**加载，宁可启动慢 30s，也不要让前几个请求卡在
    # transformers 的导入上（导入主要是 Python 层代码，会长时间占着 GIL，
    # 放在后台线程里预热时，事件循环照样被饿住 —— 实测请求会超时）。
    if config.EMBED_WARM_ON_START:
        try:
            from embed import get_embedder

            info = get_embedder().warm()
            _health_cache["data"] = None          # 让健康检查反映预热后的真实状态
            print(f"[web] embedding 就绪: {info}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[web] embedding 预热失败: {exc}", flush=True)

    print(json.dumps(_health_payload(), ensure_ascii=False, indent=2), flush=True)
    yield
    for sub in state.subs:
        sub.stop()
    if state.workers:
        for sub in state.workers.get("subs", []):
            sub.stop()
    bus.close()


app = FastAPI(title="多模态 RAG 问答（Kafka + MinerU）", version="1.0", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for blk in iter(lambda: fh.read(1 << 20), b""):
            h.update(blk)
    return h.hexdigest()


def _doc_public(d: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "doc_id": d["doc_id"],
        "filename": d.get("filename"),
        "size_bytes": d.get("size_bytes") or 0,
        "pages": d.get("n_pages") or d.get("pages") or 0,
        "state": d.get("state"),
        "state_label": messages.STATE_LABELS.get(d.get("state") or "", d.get("state") or ""),
        "stage": d.get("stage"),
        "stage_label": d.get("stage_label") or "",
        "progress": round(float(d.get("progress") or 0), 1),
        "message": d.get("message") or "",
        "parser": d.get("parser") or "",
        "n_chunks": d.get("n_chunks") or 0,
        "n_images": d.get("n_images") or 0,
        "error": d.get("error") or "",
        "uploaded_at": d.get("uploaded_at"),
        "updated_at": d.get("updated_at"),
        "finished_at": d.get("finished_at"),
    }


_HEALTH_TTL = 4.0          # 秒；前端一进来会连着打几个接口，别每次重建 Kafka admin
_health_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def _health_payload(force: bool = False) -> Dict[str, Any]:
    now = time.time()
    if not force and _health_cache["data"] and now - _health_cache["ts"] < _HEALTH_TTL:
        return _health_cache["data"]
    bus = state.bus
    store = state.store
    out: Dict[str, Any] = {"config": config.summary()}
    try:
        out["bus"] = (_with_timeout(bus.status, 6.0,
                                    {"backend": "kafka" if getattr(bus, "kafka_mode", False) else "local",
                                     "error": "bus.status() 超时（broker 可能正在恢复）"})
                      if bus else {"backend": "not-started"})
    except Exception as exc:  # noqa: BLE001
        out["bus"] = {"error": str(exc)}
    try:
        out["store"] = store.size_stats() if store else {}
    except Exception as exc:  # noqa: BLE001
        out["store"] = {"error": str(exc)}
    from parsers import mineru_probe

    out["mineru"] = mineru_probe()
    try:
        from embed import get_embedder

        out["embedding"] = get_embedder().info()
    except Exception as exc:  # noqa: BLE001
        out["embedding"] = {"error": str(exc)}
    try:
        from llm import get_llm

        out["llm"] = get_llm().info()
    except Exception as exc:  # noqa: BLE001
        out["llm"] = {"error": str(exc)}
    out["workers_inproc"] = config.WORKERS_INPROC
    out["max_upload_mb"] = config.MAX_UPLOAD_MB
    out["sample_available"] = (BASE_DIR / "samples" / "demo_multimodal_rag.pdf").exists()
    out["uptime_sec"] = round(time.time() - state.started_at, 1)
    _health_cache["ts"] = now
    _health_cache["data"] = out
    return out


def _with_timeout(fn, timeout: float = 5.0, default: Any = None) -> Any:
    """给「可能永久阻塞」的调用套一层硬超时。

    kafka-python 的 KafkaAdminClient.list_topics()/list_consumer_groups() **没有**逐调用超时，
    broker 重启后 coordinator 恢复期间可能一直等下去。健康检查在 lifespan 和请求里都会调它，
    一旦卡住就是「端口起了但一个请求都不回」—— 所以这里用线程 + 队列强制限时。
    """
    box: "queue.Queue" = queue.Queue(maxsize=1)

    def _run() -> None:
        try:
            box.put(("ok", fn()))
        except Exception as exc:  # noqa: BLE001
            box.put(("err", f"{type(exc).__name__}: {exc}"))

    t = threading.Thread(target=_run, daemon=True, name="with-timeout")
    t.start()
    try:
        kind, value = box.get(timeout=timeout)
    except queue.Empty:
        return default if default is not None else {"error": f"超过 {timeout}s 未返回"}
    return value if kind == "ok" else {"error": value}


def _sse(event: Dict[str, Any]) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 页面与静态资源
# ---------------------------------------------------------------------------
@app.get("/")
async def index() -> FileResponse:
    page = STATIC_DIR / "index.html"
    if not page.exists():
        raise HTTPException(500, f"缺少前端页面 {page}")
    return FileResponse(str(page), media_type="text/html")


@app.get("/favicon.ico")
async def favicon() -> JSONResponse:
    return JSONResponse({}, status_code=204)


static_mount = STATIC_DIR
static_mount.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(static_mount)), name="static")


@app.get("/api/health")
def health(refresh: int = 0) -> JSONResponse:
    return JSONResponse(_health_payload(force=bool(refresh)))


@app.get("/api/pipeline")
def pipeline(recent: int = 30) -> JSONResponse:
    """链路可视化：最近的状态消息 + 总线指标（谁在产、谁在消费、有没有堆积）。"""
    bus = state.bus or get_bus()
    info = _with_timeout(bus.status, 6.0, {"error": "bus.status() 超时"})
    return JSONResponse({
        "backend": info.get("backend"),
        "published": info.get("published"),
        "consumed": info.get("consumed"),
        "topics": info.get("topics"),
        "consumer_groups": info.get("consumer_groups"),
        "subscriptions": info.get("subscriptions"),
        "recent_status": list(reversed(state.recent_status[-int(recent):])) if state.recent_status else [],
        "log": bus.recent_log(15),
    })


# ---------------------------------------------------------------------------
# 功能 1：多 PDF 上传 -> Kafka
# ---------------------------------------------------------------------------
@app.post("/api/documents")
async def upload_documents(
    files: List[UploadFile] = File(..., description="一个或多个 PDF"),
    force: int = Form(0, description="1=即使内容重复也重新入库"),
) -> JSONResponse:
    bus = state.bus or get_bus()
    store = state.store or get_store()
    accepted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []

    for uf in files or []:
        name = (uf.filename or "").strip() or "unnamed.pdf"
        suffix = Path(name).suffix.lower()
        if suffix not in config.ALLOWED_SUFFIXES:
            failed.append({"filename": name, "error": f"只支持 PDF，收到的是 {suffix or '无后缀'}"})
            continue

        doc_id = messages.new_id("doc_")
        target = config.UPLOAD_DIR / f"{doc_id}{suffix}"
        size = 0
        try:
            with open(target, "wb") as fh:
                while True:
                    chunk = await uf.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > config.MAX_UPLOAD_MB * 1024 * 1024:
                        raise HTTPException(413, f"超过 {config.MAX_UPLOAD_MB}MB 上限")
                    fh.write(chunk)
        except HTTPException as exc:
            target.unlink(missing_ok=True)
            failed.append({"filename": name, "error": str(exc.detail)})
            continue
        except Exception as exc:  # noqa: BLE001
            target.unlink(missing_ok=True)
            failed.append({"filename": name, "error": f"保存失败：{exc}"})
            continue
        finally:
            await uf.close()

        if size < 1024 or not target.read_bytes()[:5].startswith(b"%PDF-"):
            target.unlink(missing_ok=True)
            failed.append({"filename": name, "error": "文件不是有效的 PDF（缺 %PDF 头）"})
            continue

        # ── 同步部分（sha256 / pdfplumber 取页数 / SQLite / Kafka 投递）必须挪出事件循环 ──
        # 这些都是 CPU 或阻塞 I/O：直接在 async handler 里做，会把整个事件循环卡住，
        # 机器一忙就表现为「上传接口几十秒不返回」（实测踩过）。
        def _digest_store_publish() -> Dict[str, Any]:
            sha_local = _sha(target)
            pages_local = 0
            try:
                from parsers.local_pdf import page_count

                pages_local = page_count(str(target))
            except Exception as exc:  # noqa: BLE001
                print(f"[web] 页数读取失败 {name}: {exc}", flush=True)

            dup = store.find_document_by_sha(sha_local)
            if dup and not force and dup.get("state") in ("done", "parsing", "pending"):
                target.unlink(missing_ok=True)
                return {"duplicate": {
                    "filename": name, "existing_doc_id": dup["doc_id"], "state": dup["state"],
                    "reason": "内容完全相同的文档已在知识库中（要重新解析请用 force=1）",
                }}

            store.create_document(doc_id=doc_id, filename=name, pdf_path=str(target),
                                  size_bytes=size, sha256=sha_local, pages=pages_local)
            # 投递 Kafka：key=doc_id 保证同一文档的顺序
            bus.publish(config.TOPIC_UPLOAD,
                        messages.upload_msg(doc_id, name, str(target), size, sha_local,
                                            pages_local), key=doc_id)
            bus.publish(config.TOPIC_STATUS,
                        messages.upload_accepted_msg(doc_id, name, config.TOPIC_UPLOAD),
                        key=doc_id)
            bus.flush(timeout=8)
            return {"doc_id": doc_id, "filename": name, "size_bytes": size, "pages": pages_local}

        outcome = await asyncio.to_thread(_digest_store_publish)
        if "duplicate" in outcome:
            skipped.append(outcome["duplicate"])
            continue
        accepted.append(outcome)

    docs_snapshot = await asyncio.to_thread(
        lambda: [_doc_public(d) for d in store.list_documents()]
    )
    return JSONResponse({
        "accepted": accepted, "skipped": skipped, "failed": failed,
        "topic": config.TOPIC_UPLOAD,
        "backend": _with_timeout(lambda: (state.bus or get_bus()).status().get("backend"), 6.0, "unknown"),
        "documents": docs_snapshot,
    })


@app.get("/api/documents")
def list_documents(state_filter: str = "") -> JSONResponse:
    store = state.store or get_store()
    docs = store.list_documents(state_filter) if state_filter else store.list_documents()
    return JSONResponse({"documents": [_doc_public(d) for d in docs],
                         "counters": store.counters()})


@app.get("/api/documents/{doc_id}")
def document_detail(doc_id: str, limit: int = 40) -> JSONResponse:
    store = state.store or get_store()
    d = store.get_document(doc_id)
    if not d:
        raise HTTPException(404, f"没有这个文档：{doc_id}")
    chunks = store.chunks_of_doc(doc_id)[:limit]
    images = store.list_images(doc_id)
    art = Path(d.get("artifact_dir") or "")
    md_path = art / "full.md" if art else None
    return JSONResponse({
        "document": _doc_public(d),
        "chunks": [{"chunk_id": c["chunk_id"], "page": c["page"], "kind": c["kind"],
                    "heading": c.get("heading") or "", "text": (c["text"] or "")[:800],
                    "image_id": c.get("image_id")} for c in chunks],
        "images": [{"image_id": i["image_id"], "page": i["page"], "caption": i["caption"],
                    "url": f"/api/images/{i['image_id']}", "width": i["width"],
                    "height": i["height"], "source": i["source"]} for i in images],
        "markdown_available": bool(md_path and md_path.exists()),
    })


@app.post("/api/documents/{doc_id}/reparse")
def reparse_document(doc_id: str) -> JSONResponse:
    bus = state.bus or get_bus()
    store = state.store or get_store()
    d = store.get_document(doc_id)
    if not d:
        raise HTTPException(404, f"没有这个文档：{doc_id}")
    from store import abs_of

    pdf = abs_of(d.get("pdf_path") or "")
    if not pdf or not pdf.exists():
        raise HTTPException(410, f"原始 PDF 已不在：{pdf}，请重新上传")
    bus.publish(config.TOPIC_UPLOAD,
                messages.upload_msg(doc_id, d["filename"], str(pdf), d.get("size_bytes") or 0,
                                    d.get("sha256") or "", d.get("pages") or 0), key=doc_id)
    bus.publish(config.TOPIC_STATUS, messages.upload_accepted_msg(doc_id, d["filename"],
                                                                  config.TOPIC_UPLOAD), key=doc_id)
    bus.flush(timeout=8)
    return JSONResponse({"ok": True, "doc_id": doc_id, "requeued": True})


@app.delete("/api/documents/{doc_id}")
def delete_document(doc_id: str, purge_files: int = 1) -> JSONResponse:
    store = state.store or get_store()
    if not store.get_document(doc_id):
        raise HTTPException(404, f"没有这个文档：{doc_id}")
    res = store.delete_document(doc_id, purge_files=bool(purge_files))
    return JSONResponse({"ok": True, **res})


# ---------------------------------------------------------------------------
# 功能 2：解析状态实时推送（SSE）
# ---------------------------------------------------------------------------
@app.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    """把 Kafka ``mmrag.doc.status`` 转成 SSE。

    事件类型（``type`` 字段）：
    * ``snapshot`` 首帧，给当前所有文档状态，避免「先订阅后加载」的空窗
    * ``status``   单个文档的状态变化（pending/parsing/done/failed + progress + message）
    * ``ping``     15s 心跳，防代理断连
    """
    store = state.store or get_store()
    q: asyncio.Queue = asyncio.Queue(maxsize=2000)
    state.status_listeners.add(q)

    async def gen():
        try:
            snap = {"type": "snapshot", "ts": time.time(),
                    "documents": [_doc_public(d) for d in store.list_documents()],
                    "counters": store.counters()}
            yield _sse(snap)
            last_ping = time.time()
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    if time.time() - last_ping > 15:
                        last_ping = time.time()
                        yield _sse({"type": "ping", "ts": time.time()})
                    continue
                yield _sse({"type": "status", **payload})
                if payload.get("state") in ("done", "failed"):
                    # 状态落定后顺便同步一次计数，前端徽标不用另外拉接口
                    yield _sse({"type": "counters", "counters": store.counters()})
        finally:
            state.status_listeners.discard(q)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# 功能 3：多模态问答（提问可带图，回答可带图）
# ---------------------------------------------------------------------------
@app.post("/api/qa")
async def qa(
    request: Request,
    question: str = Form(...),
    session_id: str = Form("default"),
    doc_ids: str = Form("", description="逗号分隔，限定检索范围；留空=全库"),
    top_k: int = Form(0),
    files: Optional[List[UploadFile]] = File(None, description="随提问一起送进模型的图片"),
) -> StreamingResponse:
    """**POST + SSE**：请求体是 multipart（可带图），响应是一条 SSE 流。

    事件 ``type``：``accepted`` → ``stage`` → ``delta``* → ``final`` / ``error``。
    """
    q_text = (question or "").strip()
    if not q_text:
        raise HTTPException(422, "问题不能为空")

    bus = state.bus or get_bus()
    store = state.store or get_store()
    rid = messages.new_id("req_")

    # 1) 用户图片落盘（Kafka 里只传路径，见 messages.py）
    saved: List[Dict[str, Any]] = []
    if files:
        out_dir = config.UPLOAD_DIR / "qa" / rid
        out_dir.mkdir(parents=True, exist_ok=True)
        for uf in files:
            name = (uf.filename or "").strip() or f"img{len(saved) + 1}.png"
            suffix = Path(name).suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise HTTPException(415, f"图片格式不支持：{suffix or name}")
            dest = out_dir / f"{len(saved) + 1}_{uuid.uuid4().hex[:6]}{suffix}"
            size = 0
            try:
                with open(dest, "wb") as fh:
                    while True:
                        blk = await uf.read(1 << 20)
                        if not blk:
                            break
                        size += len(blk)
                        if size > MAX_QA_IMAGE_MB * 1024 * 1024:
                            raise HTTPException(413, f"单张图片超过 {MAX_QA_IMAGE_MB}MB")
                        fh.write(blk)
            except HTTPException:
                dest.unlink(missing_ok=True)
                raise
            await uf.close()
            saved.append({"name": name, "path": str(dest), "size_bytes": size})

    ids = [s.strip() for s in (doc_ids or "").split(",") if s.strip()]
    q: asyncio.Queue = asyncio.Queue(maxsize=4000)
    state.qa_listeners[rid] = q

    bus.publish(config.TOPIC_QA_REQ,
                messages.qa_request_msg(rid, session_id, q_text, saved, ids, top_k or None),
                key=rid)
    bus.flush(timeout=8)

    async def gen():
        try:
            yield _sse({"type": "accepted", "request_id": rid, "session_id": session_id,
                        "question": q_text, "images": [im["name"] for im in saved],
                        "backend": bus.status().get("backend")})
            deadline = time.time() + config.LLM_TIMEOUT + 240
            while True:
                if await request.is_disconnected():
                    break
                if time.time() > deadline:
                    yield _sse({"type": "error", "error": "等待 QA Worker 响应超时"})
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=5.0)
                except asyncio.TimeoutError:
                    yield _sse({"type": "ping", "ts": time.time()})
                    continue
                kind = payload.get("kind")
                body = {k: v for k, v in payload.items() if k not in ("type", "request_id")}
                yield _sse({"type": kind, "request_id": rid, **body})
                if kind in ("answer", "error"):
                    break
        finally:
            state.qa_listeners.pop(rid, None)
            shutil.rmtree(config.UPLOAD_DIR / "qa" / rid, ignore_errors=True)

    return StreamingResponse(
        gen(), media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive",
                 "X-Accel-Buffering": "no"},
    )


@app.get("/api/qa/history")
def qa_history(session_id: str = "default", limit: int = 30) -> JSONResponse:
    store = state.store or get_store()
    return JSONResponse({"session_id": session_id,
                         "turns": store.list_qa_turns(session_id, limit=limit)})


@app.get("/api/search")
def search(q: str, top_k: int = 0, doc_ids: str = "") -> JSONResponse:
    if not q.strip():
        raise HTTPException(422, "缺少参数 q")
    import rag
    from embed import get_embedder

    ids = [s.strip() for s in doc_ids.split(",") if s.strip()]
    t0 = time.time()
    hits = rag.search_only(q, top_k or config.RETRIEVAL_TOP_K, ids or None)
    return JSONResponse({"query": q, "took_ms": int((time.time() - t0) * 1000),
                        "embedding": get_embedder().info(),
                        "index_vectors": (state.store or get_store()).vector_count(),
                        "hits": hits})


@app.get("/api/sample")
def sample_pdf() -> FileResponse:
    """前端「载入示例文档」按钮的取件口（文件由 scripts/gen_sample_pdf.py 生成）。"""
    p = BASE_DIR / "samples" / "demo_multimodal_rag.pdf"
    if not p.exists():
        raise HTTPException(404, "示例 PDF 不存在，请先运行  python scripts/gen_sample_pdf.py")
    return FileResponse(str(p), media_type="application/pdf", filename=p.name)


@app.get("/api/images/{image_id}")
def image(image_id: int) -> FileResponse:
    store = state.store or get_store()
    path = store.image_abs_path(image_id)
    if not path:
        raise HTTPException(404, f"图片不存在：image_id={image_id}（文档可能已删除）")
    return FileResponse(str(path), headers={"Cache-Control": "public, max-age=86400"})


@app.get("/api/documents/{doc_id}/markdown")
def document_markdown(doc_id: str) -> FileResponse:
    """解析出来的整篇 markdown（排查解析质量、也是「存储」的直观证据）。"""
    store = state.store or get_store()
    d = store.get_document(doc_id)
    if not d:
        raise HTTPException(404, f"没有这个文档：{doc_id}")
    md = Path(d.get("artifact_dir") or "") / "full.md"
    if not md.exists():
        raise HTTPException(404, "该文档还没有解析产物")
    return FileResponse(str(md), media_type="text/plain; charset=utf-8")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host=config.HOST, port=config.PORT, reload=False, log_level="info")
