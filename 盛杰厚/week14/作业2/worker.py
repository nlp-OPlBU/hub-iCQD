"""解析 / 入库 / 问答 Worker —— Kafka 消费端（功能 1、2、3 的执行者）。

三个消费角色（可以同进程，也可以拆成三个进程横向扩容）
=========================================================
==== 角色 ====  ==== 订阅 topic ====  ==== 生产 topic ====  ==== 干什么 ====
parse         mmrag.pdf.upload      mmrag.pdf.parsed      调 MinerU（或内置解析器）解析 PDF
                                        + mmrag.doc.status  产出 blocks + 图片，落盘 artifact
index         mmrag.pdf.parsed      (mmrag.doc.status)    图片入库 → 视觉模型生成图片描述
                                                        → bge 向量化 → 写 FAISS
qa            mmrag.qa.request      mmrag.qa.answer       多模态检索 + 百炼生成，**流式**发增量

三段之间只靠 Kafka 传递，状态变化统一发到 ``mmrag.doc.status``，Web 进程订阅它再 SSE 推前端，
所以「解析中」的进度是**真的从 Worker 进程发出来的**，不是前端假动画。

顺序性保证
----------
``key=doc_id`` / ``key=request_id``：同一文档、同一请求的消息进同一分区，被同一消费者按序处理，
不会出现「解析完成」比「解析中」先到、把状态机倒着推的情况（store.apply_status 里还有一道单调性保护）。

单独运行（横向扩容 / 排障很有用）
--------------------------------
    python worker.py --roles parse          # 只跑解析
    python worker.py --roles index,qa       # 只跑入库和问答
    python worker.py --roles all            # 默认，全部（Web 进程内默认也是这个）
"""
from __future__ import annotations

import argparse
import json
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

import config
import messages
from bus import MessageBus, get_bus
from store import Store, get_store


class WorkerError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# 进度上报：把 Worker 内部细粒度进度节流成状态消息
# ---------------------------------------------------------------------------
class Reporter:
    """把 (state, stage, progress) 发到 mmrag.doc.status，并同步落库。

    节流规则：进度增量 >= ``min_delta`` 或距上次 >= ``min_interval`` 秒才发一条，
    否则一个 100 页 PDF 会把 topic 灌满重复消息。
    """

    def __init__(self, bus: MessageBus, store: Store, doc_id: str, filename: str,
                 min_delta: float = 2.0, min_interval: float = 0.8):
        self.bus = bus
        self.store = store
        self.doc_id = doc_id
        self.filename = filename
        self.min_delta = min_delta
        self.min_interval = min_interval
        self._last_pct = -100.0
        self._last_ts = 0.0
        self._lock = threading.Lock()

    def emit(self, state: str, stage: str, progress: Optional[float] = None,
             message: str = "", force: bool = True, **extra: Any) -> None:
        pct = progress if progress is not None else messages.STAGE_PROGRESS.get(stage, 0.0)
        now = time.time()
        with self._lock:
            if not force:
                if pct - self._last_pct < self.min_delta and now - self._last_ts < self.min_interval:
                    return
            self._last_pct, self._last_ts = pct, now
            msg = messages.status_msg(self.doc_id, state, stage=stage, progress=pct,
                                      message=message, filename=self.filename, **extra)
            self.bus.publish(config.TOPIC_STATUS, msg, key=self.doc_id)
        # 落库由 Web 侧订阅者做，但 Worker 自己也更新一次：
        # 这样即使 Web 进程没在跑（比如只起 worker.py 调试），数据库里的状态也是对的
        self.store.apply_status(msg)

    def progress_cb(self, state: str = messages.STATE_PARSING,
                    stage: str = "mineru") -> Callable[[float, str], None]:
        """给解析器用的 callback(percent, message)。"""

        def _cb(pct: float, text: str = "") -> None:
            self.emit(state, stage, progress=pct, message=text, force=False)

        return _cb


# ---------------------------------------------------------------------------
# 角色 1：解析（MinerU 优先，失败按配置降级到内置解析器）
# ---------------------------------------------------------------------------
class ParseStage:
    def __init__(self, bus: Optional[MessageBus] = None, store: Optional[Store] = None):
        self.bus = bus or get_bus()
        self.store = store or get_store()
        self.processed = 0
        self.failed = 0

    def handle(self, msg: Dict[str, Any], key: Optional[str] = None) -> None:
        if not messages.check(msg, "doc_id", "pdf_path"):
            print("[parse] 忽略非法消息:", str(msg)[:200])
            return
        doc_id = msg["doc_id"]
        filename = msg.get("filename") or Path(msg["pdf_path"]).name
        rep = Reporter(self.bus, self.store, doc_id, filename)
        t0 = time.time()
        pdf_path = msg["pdf_path"]
        if not Path(pdf_path).exists():
            rep.emit(messages.STATE_FAILED, "error", message=f"上传文件不见了：{pdf_path}",
                     error="文件不存在")
            self.failed += 1
            return

        rep.emit(messages.STATE_PARSING, "mineru", progress=3.0,
                 message=f"开始解析（{filename}，{msg.get('size_bytes', 0) / 1e6:.1f}MB）")
        try:
            from parsers import mineru_probe, parse_pdf

            info = mineru_probe(force=True)
            if info.get("ok"):
                rep._last_pct = -100
                rep.emit(messages.STATE_PARSING, "mineru", progress=5.0,
                         message=f"MinerU 服务在线（{info.get('backend')}），提交解析")
            else:
                if config.MINERU_STRICT or (config.PREFERRED_PARSER == "mineru"):
                    raise WorkerError(
                        f"MinerU 不可用：{info.get('detail')}。"
                        "启动它：pip install -U 'mineru[core]' && mineru-api --port 8002"
                    )
                rep.emit(messages.STATE_PARSING, "mineru_fallback", progress=6.0,
                         message=f"MinerU 不可用（{info.get('detail') or '未启动'}），用内置解析器")

            doc = parse_pdf(pdf_path, doc_id, filename, progress_cb=rep.progress_cb(
                messages.STATE_PARSING,
                "mineru" if info.get("ok") else "mineru_fallback"))
            artifact = doc.save()
            stats = doc.stats()
            self.processed += 1
            rep.emit(
                messages.STATE_PARSING, "images", progress=42.0,
                message=(f"解析完成（{stats['parser']}）：{stats['pages']} 页 / "
                         f"{stats['text_blocks']} 文本块 / {stats['table_blocks']} 表格 / "
                         f"{stats['images']} 图，用时 {time.time() - t0:.1f}s"),
                parser=stats["parser"], n_pages=stats["pages"], n_images=stats["images"],
                artifact_dir=artifact,
            )
            handoff = messages.parsed_msg(
                doc_id=doc_id, filename=filename, artifact_dir=artifact,
                parser=stats["parser"], pages=stats["pages"],
                n_text_blocks=stats["text_blocks"] + stats["table_blocks"],
                n_images=stats["images"],
            )
            self.bus.publish(config.TOPIC_PARSED, handoff, key=doc_id)
            self.bus.flush()
        except Exception as exc:  # noqa: BLE001
            self.failed += 1
            detail = f"{type(exc).__name__}: {exc}"
            traceback.print_exc()
            rep.emit(messages.STATE_FAILED, "error", message=f"解析失败：{detail[:220]}",
                     error=detail[:500], force=True)

    def subscribe(self) -> Any:
        return self.bus.subscribe(config.TOPIC_UPLOAD, self.handle, group=config.GROUP_PARSE,
                                  earliest=True, name="parse")


# ---------------------------------------------------------------------------
# 角色 2：入库（图片描述 + 切块 + bge 向量化 + 写 FAISS）
# ---------------------------------------------------------------------------
class IndexStage:
    def __init__(self, bus: Optional[MessageBus] = None, store: Optional[Store] = None):
        self.bus = bus or get_bus()
        self.store = store or get_store()
        self.processed = 0

    def handle(self, msg: Dict[str, Any], key: Optional[str] = None) -> None:
        if not messages.check(msg, "doc_id", "artifact_dir"):
            print("[index] 忽略非法消息:", str(msg)[:200])
            return
        doc_id, filename = msg["doc_id"], msg.get("filename") or ""
        rep = Reporter(self.bus, self.store, doc_id, filename)
        t0 = time.time()
        try:
            from chunking import build_units
            from parsers.common import ParsedDoc

            doc = ParsedDoc.load(msg["artifact_dir"])
            # 重新解析时先清掉旧内容（向量 + chunks + images），避免同一文档命中两份
            purged = self.store.purge_doc_content(doc_id)
            if purged["chunks"]:
                print(f"[index] 重新解析：已清除旧内容 {purged}")

            # ---- 1) 图片入库（先落库拿到 image_id，问答时才能用 /api/images/{id} 回给前端）
            rep.emit(messages.STATE_PARSING, "images", progress=46.0,
                     message=f"登记 {len(doc.images)} 张配图")
            ref_to_id: Dict[str, int] = {}
            for im in doc.images:
                if not im.path or not Path(im.path).exists():
                    continue
                iid = self.store.add_image(
                    doc_id=doc_id, path=im.path, page=im.page, ref=im.ref, caption=im.caption,
                    width=im.width, height=im.height, source=im.source or doc.parser,
                )
                ref_to_id[im.ref] = iid

            # ---- 2) 切块
            units = build_units(doc)
            rep.emit(messages.STATE_PARSING, "chunking", progress=52.0,
                     message=f"切分得到 {len(units)} 个检索单元")
            if not units:
                raise WorkerError("解析结果里没有任何可入库的内容")

            # ---- 3) 图片描述（关键：让图片进入文本向量空间，从而「图片可被检索」）
            from llm import get_llm

            model = get_llm()
            if config.IMAGE_CAPTION and not model.mock:
                todo = [u for u in units if u.kind == "image" and u.image_ref in ref_to_id
                        and not (u.text or "").strip()]
                if todo:
                    rep.emit(messages.STATE_PARSING, "captioning", progress=56.0,
                             message=f"用视觉模型给 {len(todo)} 张配图补描述…")
                for i, u in enumerate(todo, 1):
                    iid = ref_to_id[u.image_ref]
                    path = self.store.image_abs_path(iid)
                    cap = model.caption_image(str(path) if path else "", hint="")
                    if cap:
                        u.text = cap
                        self.store.update_image_caption(iid, cap)
                    if i % 3 == 0 or i == len(todo):
                        pct = 56.0 + 14.0 * i / max(1, len(todo))
                        rep.emit(messages.STATE_PARSING, "captioning", progress=pct,
                                 message=f"图片描述 {i}/{len(todo)}", force=False)
            elif config.IMAGE_CAPTION and model.mock:
                missing = sum(1 for u in units if u.kind == "image" and not (u.text or "").strip())
                if missing:
                    rep.emit(messages.STATE_PARSING, "captioning", progress=68.0,
                             message=f"{missing} 张图只有位置信息没有题注（未配百炼 Key，跳过描述）",
                             force=True)

            # ---- 4) 写 chunks + 向量化
            rows = []
            for u in units:
                image_id = ref_to_id.get(u.image_ref) if u.kind == "image" else None
                if u.kind == "image" and image_id is None:
                    # 图片文件没落成的块：降级为文本块，内容不丢
                    u.kind = "text"
                    u.text = u.text or "（图片未能保存）"
                rows.append({
                    "doc_id": doc_id, "page": u.page, "kind": u.kind,
                    "text": u.text, "heading": u.heading,
                    "embed_text": u.embed_text(), "image_id": image_id, "ord": u.order,
                })
            chunk_ids = self.store.add_chunks(rows)
            rep.emit(messages.STATE_PARSING, "embedding", progress=72.0,
                     message=f"bge 向量化 {len(rows)} 条…")

            from embed import get_embedder

            emb = get_embedder()
            t_vec = time.time()
            vecs = emb.embed_docs(
                [r["embed_text"] for r in rows],
                progress_cb=lambda done, total: rep.emit(
                    messages.STATE_PARSING, "embedding",
                    progress=72.0 + 18.0 * done / max(1, total),
                    message=f"向量化 {done}/{total}", force=False),
            )
            self.store.add_vectors(vecs, chunk_ids)
            self.store.save_index()
            rep.emit(messages.STATE_PARSING, "indexing", progress=95.0,
                     message=f"写入 FAISS（{vecs.shape[0]} 条 {vecs.shape[1]} 维，"
                             f"用时 {time.time() - t_vec:.1f}s）")

            n_images = len(ref_to_id)
            self.processed += 1
            rep.emit(
                messages.STATE_DONE, "indexed", progress=100.0,
                message=(f"解析完成：{len(chunk_ids)} 个检索单元 / {n_images} 张配图 / "
                         f"用时 {time.time() - t0:.1f}s"),
                n_chunks=len(chunk_ids), n_images=n_images, parser=doc.parser,
                force=True,
            )
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            detail = f"{type(exc).__name__}: {exc}"
            rep.emit(messages.STATE_FAILED, "error", message=f"入库失败：{detail[:220]}",
                     error=detail[:500], force=True)

    def subscribe(self) -> Any:
        return self.bus.subscribe(config.TOPIC_PARSED, self.handle, group=config.GROUP_INDEX,
                                  earliest=True, name="index")


# ---------------------------------------------------------------------------
# 角色 3：多模态问答（流式发到 mmrag.qa.answer）
# ---------------------------------------------------------------------------
class QAStage:
    def __init__(self, bus: Optional[MessageBus] = None, store: Optional[Store] = None):
        self.bus = bus or get_bus()
        self.store = store or get_store()
        self.answered = 0

    def handle(self, msg: Dict[str, Any], key: Optional[str] = None) -> None:
        if not messages.check(msg, "request_id", "question"):
            print("[qa] 忽略非法消息:", str(msg)[:200])
            return
        rid = msg["request_id"]
        import rag

        try:
            for kind, payload in rag.answer_events(
                request_id=rid,
                session_id=msg.get("session_id") or "default",
                question=msg["question"],
                images=msg.get("images") or [],
                doc_ids=msg.get("doc_ids") or None,
                top_k=msg.get("top_k"),
            ):
                if kind == "stage":
                    self.bus.publish(config.TOPIC_QA_ANS, messages.qa_stage_msg(
                        rid, payload.get("stage", ""), payload.get("message", ""),
                        **{k: v for k, v in payload.items() if k not in ("stage", "message")}
                    ), key=rid)
                elif kind == "delta":
                    self.bus.publish(config.TOPIC_QA_ANS,
                                     messages.qa_delta_msg(rid, payload["delta"]), key=rid)
                elif kind == "final":
                    self.bus.publish(config.TOPIC_QA_ANS, messages.qa_final_msg(
                        rid, payload["answer"], payload["citations"], payload["used_images"],
                        payload["model"], payload["mock"], payload["stats"]), key=rid)
                    self.answered += 1
                elif kind == "error":
                    self.bus.publish(config.TOPIC_QA_ANS,
                                     messages.qa_error_msg(rid, payload.get("error", "未知错误")),
                                     key=rid)
            self.bus.flush()
        except Exception as exc:  # noqa: BLE001
            traceback.print_exc()
            self.bus.publish(config.TOPIC_QA_ANS,
                             messages.qa_error_msg(rid, f"{type(exc).__name__}: {exc}"), key=rid)
            self.bus.flush()

    def subscribe(self) -> Any:
        return self.bus.subscribe(config.TOPIC_QA_REQ, self.handle, group=config.GROUP_QA,
                                  earliest=True, name="qa")


# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------
def start(roles: Sequence[str] = ("parse", "index", "qa"),
          bus: Optional[MessageBus] = None, store: Optional[Store] = None) -> Dict[str, Any]:
    bus = bus or get_bus()
    store = store or get_store()
    roles = ["parse", "index", "qa"] if "all" in roles else list(roles)
    subs: List[Any] = []
    stages: Dict[str, Any] = {}
    if "parse" in roles:
        stages["parse"] = ParseStage(bus, store)
        subs.append(stages["parse"].subscribe())
    if "index" in roles:
        stages["index"] = IndexStage(bus, store)
        subs.append(stages["index"].subscribe())
    if "qa" in roles:
        stages["qa"] = QAStage(bus, store)
        subs.append(stages["qa"].subscribe())
    return {"stages": stages, "subs": subs, "roles": roles, "bus": bus}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="多模态 RAG Worker（Kafka 消费端）")
    ap.add_argument("--roles", default="all",
                    help="parse,index,qa 或 all（默认 all）")
    ap.add_argument("--seconds", type=float, default=0, help="跑多久后退出，0=常驻")
    ap.add_argument("--check", action="store_true", help="只打印环境与后端信息")
    args = ap.parse_args(argv)

    bus = get_bus()
    bus.ensure_topics()
    store = get_store()
    print(json.dumps({"kafka": bus.status().get("backend"), "topics": bus.status().get("topics"),
                      "store": store.size_stats(), "config": config.summary()},
                     ensure_ascii=False, indent=2))
    if args.check:
        from llm import get_llm
        from embed import get_embedder

        print("embedding:", get_embedder().warm())
        print("llm:", get_llm().info())
        return 0

    handles = start([r.strip() for r in args.roles.split(",") if r.strip()], bus, store)
    print(f"[worker] 已启动角色 {handles['roles']}，后端 {bus.status()['backend']}，Ctrl-C 退出")
    t0 = time.time()
    try:
        while True:
            time.sleep(2)
            if args.seconds and time.time() - t0 > args.seconds:
                break
    except KeyboardInterrupt:
        print("[worker] 收到 Ctrl-C，正在退出…")
    for sub in handles["subs"]:
        sub.stop()
    bus.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
