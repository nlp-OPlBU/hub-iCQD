"""存储层：SQLite（元数据 + 状态）+ FAISS（向量索引）+ 图片文件。

为什么是这一套
--------------
* **状态必须落库**：前端的「待解析 / 解析中 / 解析完成」既要从 Kafka 状态 topic 实时推，
  也要能在刷新页面、重启服务后还原 —— 所以 SQLite 是状态的事实来源，Kafka 只是实时通知。
* **向量与元数据分离**：FAISS 只存向量和 ``vector_id``，文本/图片/页码这些在 SQLite，
  检索拿到 id 再回表。这样删文档可以 ``remove_ids`` 精确摘除向量。
* **图片走文件系统**：MinerU/兜底解析器抽出的图片存 ``data/images/{doc_id}/``，
  数据库里只存相对路径；问答时把图片以 ``/api/images/{id}`` 的形式嵌进答案，
  前端直接 ``<img>`` 显示 —— 这就是「图文混排输出」里图的来源。

线程模型：Web 线程读、Worker 线程写，SQLite 开 WAL + RLock 串行写，FAISS 写同样上锁。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
    doc_id       TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    pdf_path     TEXT,
    sha256       TEXT,
    size_bytes   INTEGER DEFAULT 0,
    pages        INTEGER DEFAULT 0,
    state        TEXT NOT NULL DEFAULT 'pending',   -- pending / parsing / done / failed
    stage        TEXT DEFAULT '',                   -- 细粒度阶段
    stage_label  TEXT DEFAULT '',
    progress     REAL DEFAULT 0,                    -- 0-100
    message      TEXT DEFAULT '',
    parser       TEXT DEFAULT '',                   -- mineru / pdf_local
    n_chunks     INTEGER DEFAULT 0,
    n_images     INTEGER DEFAULT 0,
    n_pages      INTEGER DEFAULT 0,
    error        TEXT DEFAULT '',
    artifact_dir TEXT DEFAULT '',
    uploaded_at  REAL NOT NULL,
    updated_at   REAL NOT NULL,
    finished_at  REAL
);
CREATE INDEX IF NOT EXISTS idx_doc_state ON documents(state);
CREATE INDEX IF NOT EXISTS idx_doc_sha   ON documents(sha256);

CREATE TABLE IF NOT EXISTS images (
    image_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     TEXT NOT NULL,
    page       INTEGER DEFAULT 0,
    path       TEXT NOT NULL,          -- 相对 DATA_DIR 的路径
    ref        TEXT DEFAULT '',        -- 解析器内部引用（MinerU 的 images/xxx.jpg）
    caption    TEXT DEFAULT '',        -- 图片描述（参与向量检索，让图片可被搜到）
    bbox       TEXT DEFAULT '',        -- json [x0,y0,x1,y1]
    width      INTEGER DEFAULT 0,
    height     INTEGER DEFAULT 0,
    source     TEXT DEFAULT '',        -- mineru / pdf_local
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_img_doc ON images(doc_id);

CREATE TABLE IF NOT EXISTS chunks (
    chunk_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id     TEXT NOT NULL,
    page       INTEGER DEFAULT 0,
    kind       TEXT NOT NULL,          -- text / table / image
    text       TEXT NOT NULL,
    heading    TEXT DEFAULT '',        -- 所属标题（上下文，用于展示与引用）
    embed_text TEXT DEFAULT '',        -- 真正被向量化的文本（含标题前缀）
    image_id   INTEGER,                -- kind=image 时指向 images
    vector_id  INTEGER,                -- FAISS 里的 id（= chunk_id）
    ord        INTEGER DEFAULT 0,      -- 文档内顺序
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id);
CREATE INDEX IF NOT EXISTS idx_chunk_kind ON chunks(kind);

CREATE TABLE IF NOT EXISTS qa_turns (
    turn_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT,
    session_id TEXT,
    role       TEXT NOT NULL,          -- user / assistant
    content    TEXT NOT NULL,
    images     TEXT DEFAULT '[]',      -- json [{image_id,name}]
    citations  TEXT DEFAULT '[]',
    mock       INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_session ON qa_turns(session_id, turn_id);
"""

_DOC_COLUMNS = [
    "filename", "pdf_path", "sha256", "size_bytes", "pages", "state", "stage", "stage_label",
    "progress", "message", "parser", "n_chunks", "n_images", "n_pages", "error",
    "artifact_dir", "updated_at", "finished_at",
]


def _rel(path: Any) -> str:
    """把绝对路径转成相对 DATA_DIR 的存储值（跨机器可移植）。"""
    if not path:
        return ""
    p = Path(str(path))
    try:
        return str(p.resolve().relative_to(config.DATA_DIR))
    except Exception:  # noqa: BLE001
        return str(p)


def abs_of(rel: str) -> Path:
    """_rel() 的反向：相对路径 -> 绝对路径。"""
    if not rel:
        return Path("")
    p = Path(rel)
    return p if p.is_absolute() else (config.DATA_DIR / p)


class Store:
    def __init__(self, dim: Optional[int] = None):
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript("PRAGMA journal_mode=WAL;PRAGMA synchronous=NORMAL;"
                                     "PRAGMA foreign_keys=ON;")
            self._conn.executescript(SCHEMA)
            self._migrate()
            self._conn.commit()
        self.dim = dim or 0
        self._index = None
        self._index_dirty = False
        self._load_index()

    # ------------------------------------------------------------ 结构升级
    def _migrate(self) -> None:
        """给老版本 DB 补列（加字段后不必删库重跑）。"""
        have = {r[1] for r in self._conn.execute("PRAGMA table_info(chunks)").fetchall()}
        for col, decl in (("heading", "TEXT DEFAULT ''"), ("embed_text", "TEXT DEFAULT ''")):
            if col not in have:
                self._conn.execute(f"ALTER TABLE chunks ADD COLUMN {col} {decl}")

    # ================================================================ documents
    def create_document(
        self, doc_id: str, filename: str, pdf_path: str, size_bytes: int, sha256: str,
        pages: int = 0, state: str = "pending", stage: str = "queued",
        stage_label: str = "排队中", progress: float = 0.0, message: str = "",
    ) -> Dict[str, Any]:
        now = time.time()
        with self._lock:
            self._conn.execute(
                """INSERT OR REPLACE INTO documents
                   (doc_id, filename, pdf_path, sha256, size_bytes, pages, state, stage,
                    stage_label, progress, message, uploaded_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, filename, _rel(pdf_path), sha256, size_bytes, pages, state, stage,
                 stage_label, progress, message, now, now),
            )
            self._conn.commit()
        return self.get_document(doc_id) or {}

    def update_document(self, doc_id: str, **fields: Any) -> None:
        fields = {k: v for k, v in fields.items() if k in _DOC_COLUMNS and v is not None}
        if not fields:
            return
        fields.setdefault("updated_at", time.time())
        sets = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._conn.execute(f"UPDATE documents SET {sets} WHERE doc_id=?",
                               (*fields.values(), doc_id))
            self._conn.commit()

    def apply_status(self, msg: Dict[str, Any]) -> None:
        """把状态消息（Kafka mmrag.doc.status 的 payload）落到 documents 上。

        幂等 + 单调：已完成的文档不会被迟到的「解析中」倒回去（跨分区/重放时可能发生）。
        """
        doc_id = msg.get("doc_id")
        if not doc_id:
            return
        order = {"pending": 0, "parsing": 1, "done": 2, "failed": 3}
        cur = self.get_document(doc_id)
        fields: Dict[str, Any] = {
            "state": msg.get("state"),
            "stage": msg.get("stage"),
            "stage_label": msg.get("stage_label"),
            "progress": msg.get("progress"),
            "message": msg.get("message"),
        }
        for k in ("parser", "n_chunks", "n_images", "n_pages", "error", "artifact_dir",
                  "filename"):
            if msg.get(k) is not None:
                fields[k] = msg[k]
        if cur:
            if order.get(fields.get("state") or "", -1) < order.get(cur.get("state") or "", -1):
                # 迟到的低优先级状态：只更新进度文案，不覆盖状态
                fields["state"] = cur.get("state")
            if fields.get("state") == "done":
                fields["finished_at"] = time.time()
            if not fields.get("filename"):
                fields.pop("filename", None)
            self.update_document(doc_id, **fields)
        else:
            # 消费顺序颠倒或历史回放：直接按消息内容建行
            self.create_document(
                doc_id=doc_id,
                filename=fields.get("filename", ""),
                pdf_path="",
                size_bytes=0,
                sha256="",
                state=fields.get("state", "pending"),
                stage=fields.get("stage", ""),
                stage_label=fields.get("stage_label", ""),
                progress=fields.get("progress", 0),
                message=fields.get("message", ""),
            )
            self.update_document(doc_id, **{k: v for k, v in fields.items()
                                            if k not in ("state", "stage", "stage_label",
                                                         "progress", "message")})

    def get_document(self, doc_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        return dict(row) if row else None

    def find_document_by_sha(self, sha256: str) -> Optional[Dict[str, Any]]:
        if not sha256:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM documents WHERE sha256=? ORDER BY uploaded_at DESC LIMIT 1",
                (sha256,),
            ).fetchone()
        return dict(row) if row else None

    def list_documents(self, state: str = "") -> List[Dict[str, Any]]:
        q = "SELECT * FROM documents"
        args: Sequence[Any] = ()
        if state:
            q += " WHERE state=?"
            args = (state,)
        q += " ORDER BY uploaded_at DESC"
        with self._lock:
            rows = self._conn.execute(q, args).fetchall()
        return [dict(r) for r in rows]

    def counters(self) -> Dict[str, int]:
        with self._lock:
            rows = self._conn.execute("SELECT state, COUNT(*) c FROM documents GROUP BY state").fetchall()
        out = {r["state"]: r["c"] for r in rows}
        return {
            "total": sum(out.values()),
            "pending": out.get("pending", 0),
            "parsing": out.get("parsing", 0),
            "done": out.get("done", 0),
            "failed": out.get("failed", 0),
        }

    def recover_stale(self, older_than: float = 0.0) -> int:
        """服务重启后，把卡在「解析中/待解析」的文档标记为失败，避免页面永远转圈。"""
        cutoff = time.time() - older_than
        with self._lock:
            rows = self._conn.execute(
                "SELECT doc_id FROM documents WHERE state IN ('pending','parsing') AND updated_at<=?",
                (cutoff,),
            ).fetchall()
            for r in rows:
                self._conn.execute(
                    "UPDATE documents SET state='failed', stage='error', stage_label='已中断',"
                    " error='服务重启时该文档仍在解析中，已标记失败；重新上传即可重试', "
                    "message='解析中断（服务重启）', updated_at=? WHERE doc_id=?",
                    (time.time(), r["doc_id"]),
                )
            self._conn.commit()
        return len(rows)

    # ==================================================================== images
    def add_image(
        self, doc_id: str, path: str, page: int = 0, ref: str = "", caption: str = "",
        bbox: Optional[Sequence[float]] = None, width: int = 0, height: int = 0,
        source: str = "",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO images (doc_id, page, path, ref, caption, bbox, width, height,
                                       source, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (doc_id, page, _rel(path), ref, caption,
                 json.dumps(list(bbox)) if bbox else "", width, height, source, time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def find_image_by_ref(self, doc_id: str, ref: str) -> Optional[Dict[str, Any]]:
        if not ref:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM images WHERE doc_id=? AND ref=? LIMIT 1", (doc_id, ref)
            ).fetchone()
        return dict(row) if row else None

    def get_image(self, image_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM images WHERE image_id=?", (image_id,)).fetchone()
        return dict(row) if row else None

    def list_images(self, doc_id: str = "") -> List[Dict[str, Any]]:
        q = "SELECT * FROM images"
        args: Sequence[Any] = ()
        if doc_id:
            q += " WHERE doc_id=?"
            args = (doc_id,)
        q += " ORDER BY image_id"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def update_image_caption(self, image_id: int, caption: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE images SET caption=? WHERE image_id=?", (caption, image_id))
            self._conn.commit()

    def image_abs_path(self, image_id: int) -> Optional[Path]:
        img = self.get_image(image_id)
        if not img:
            return None
        p = abs_of(img["path"])
        return p if p.exists() else None

    # ==================================================================== chunks
    def add_chunks(self, items: Iterable[Dict[str, Any]]) -> List[int]:
        """items: [{"doc_id","page","kind","text","heading","embed_text","image_id","ord"}] -> [chunk_id]

        ``embed_text`` 是真正进 FAISS 的文本（带标题上下文），``text`` 是展示/喂 LLM 的正文，
        两者都存下来，这样换 embedding 模型时可以 rebuild 而不用重新解析 PDF。
        """
        ids: List[int] = []
        now = time.time()
        with self._lock:
            for it in items:
                cur = self._conn.execute(
                    """INSERT INTO chunks (doc_id, page, kind, text, heading, embed_text,
                                          image_id, vector_id, ord, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (it["doc_id"], int(it.get("page") or 0), it.get("kind") or "text",
                     it.get("text") or "", it.get("heading") or "", it.get("embed_text") or "",
                     it.get("image_id"), None, int(it.get("ord") or 0), now),
                )
                cid = int(cur.lastrowid)
                self._conn.execute("UPDATE chunks SET vector_id=? WHERE chunk_id=?", (cid, cid))
                ids.append(cid)
            self._conn.commit()
        return ids

    def iter_all_embed_texts(self, limit: int = 0) -> List[Tuple[int, str]]:
        """rebuild 索引时用：[(chunk_id, embed_text)]。"""
        q = "SELECT chunk_id, embed_text, text FROM chunks ORDER BY chunk_id"
        if limit:
            q += f" LIMIT {int(limit)}"
        with self._lock:
            rows = self._conn.execute(q).fetchall()
        return [(int(r["chunk_id"]), r["embed_text"] or r["text"] or "") for r in rows]

    def get_chunk(self, chunk_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """SELECT c.*, d.filename, d.parser FROM chunks c
                   LEFT JOIN documents d USING(doc_id) WHERE c.chunk_id=?""",
                (chunk_id,),
            ).fetchone()
        return dict(row) if row else None

    def chunks_of_doc(self, doc_id: str, kind: str = "") -> List[Dict[str, Any]]:
        q = "SELECT * FROM chunks WHERE doc_id=?"
        args: List[Any] = [doc_id]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        q += " ORDER BY ord"
        with self._lock:
            return [dict(r) for r in self._conn.execute(q, args).fetchall()]

    def purge_doc_content(self, doc_id: str) -> Dict[str, int]:
        """只删文档的**索引内容**（向量 + chunks + images 行），保留 documents 行。

        重新解析同一篇文档时由 IndexStage 调用：不清就会有两份向量同时被召回。
        """
        with self._lock:
            chunk_ids = [int(r["chunk_id"]) for r in self._conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
            n_img = self._conn.execute(
                "SELECT COUNT(*) c FROM images WHERE doc_id=?", (doc_id,)).fetchone()["c"]
            self.remove_vectors(chunk_ids)
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM images WHERE doc_id=?", (doc_id,))
            self._conn.commit()
        return {"chunks": len(chunk_ids), "images": int(n_img)}

    def delete_document(self, doc_id: str, purge_files: bool = False) -> Dict[str, Any]:
        """删文档：向量先摘（FAISS），再删 chunks/images/documents 行。"""
        with self._lock:
            chunk_ids = [r["chunk_id"] for r in self._conn.execute(
                "SELECT chunk_id FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()]
            images = [dict(r) for r in self._conn.execute(
                "SELECT * FROM images WHERE doc_id=?", (doc_id,)).fetchall()]
            self.remove_vectors(chunk_ids)
            self._conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM images WHERE doc_id=?", (doc_id,))
            self._conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_id,))
            self._conn.commit()
        removed_files = 0
        if purge_files:
            import shutil

            for img in images:
                p = abs_of(img["path"])
                if p.exists():
                    try:
                        p.unlink()
                        removed_files += 1
                    except OSError:
                        pass
            doc = self.get_document(doc_id) or {}
            for d in (doc.get("artifact_dir") or "", str(Path(config.PARSED_DIR) / doc_id),
                      str(Path(config.IMAGE_DIR) / doc_id)):
                p = Path(d) if d else None
                if p and p.exists() and p.is_dir():
                    shutil.rmtree(p, ignore_errors=True)
        return {"doc_id": doc_id, "chunks": len(chunk_ids), "images": len(images),
                "files": removed_files}

    def size_stats(self) -> Dict[str, Any]:
        with self._lock:
            n_chunk = self._conn.execute("SELECT COUNT(*) c FROM chunks").fetchone()["c"]
            n_text = self._conn.execute("SELECT COUNT(*) c FROM chunks WHERE kind='text'").fetchone()["c"]
            n_table = self._conn.execute("SELECT COUNT(*) c FROM chunks WHERE kind='table'").fetchone()["c"]
            n_img_chunk = self._conn.execute("SELECT COUNT(*) c FROM chunks WHERE kind='image'").fetchone()["c"]
            n_image = self._conn.execute("SELECT COUNT(*) c FROM images").fetchone()["c"]
            n_captioned = self._conn.execute(
                "SELECT COUNT(*) c FROM images WHERE caption<>''").fetchone()["c"]
        return {
            "documents": self.counters(),
            "chunks": n_chunk,
            "chunks_text": n_text,
            "chunks_table": n_table,
            "chunks_image": n_img_chunk,
            "images": n_image,
            "images_captioned": n_captioned,
            "vectors": self.vector_count(),
            "dim": self.dim,
        }

    # ==================================================================== QA 记录
    def add_qa_turn(self, session_id: str, role: str, content: str, request_id: str = "",
                    images: Optional[List[Dict[str, Any]]] = None,
                    citations: Optional[List[Dict[str, Any]]] = None, mock: bool = False) -> int:
        with self._lock:
            cur = self._conn.execute(
                """INSERT INTO qa_turns (request_id, session_id, role, content, images,
                                         citations, mock, created_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (request_id, session_id, role, content,
                 json.dumps(images or [], ensure_ascii=False),
                 json.dumps(citations or [], ensure_ascii=False), 1 if mock else 0,
                 time.time()),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def recent_qa(self, session_id: str, turns: int = 3) -> List[Dict[str, Any]]:
        """返回最近 ``turns`` 轮（每轮 = user + assistant 两条），按时间正序。"""
        limit = max(0, turns) * 2
        if not session_id or not limit:
            return []
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM qa_turns WHERE session_id=? ORDER BY turn_id DESC LIMIT ?",
                (session_id, limit),
            ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def list_qa_turns(self, session_id: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        q = "SELECT * FROM qa_turns"
        args: List[Any] = []
        if session_id:
            q += " WHERE session_id=?"
            args.append(session_id)
        q += " ORDER BY turn_id DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = [dict(r) for r in self._conn.execute(q, args).fetchall()]
        for r in rows:
            for k in ("images", "citations"):
                try:
                    r[k] = json.loads(r.get(k) or "[]")
                except json.JSONDecodeError:
                    r[k] = []
        return list(reversed(rows))

    # ===================================================================== FAISS
    def _load_index(self) -> None:
        try:
            import faiss
        except Exception as exc:  # noqa: BLE001
            self._index = None
            self._index_error = f"faiss 不可用: {exc}"
            return
        self._index_error = ""
        if config.FAISS_PATH.exists() and config.FAISS_META_PATH.exists():
            try:
                meta = json.loads(config.FAISS_META_PATH.read_text(encoding="utf-8"))
                idx = faiss.read_index(str(config.FAISS_PATH))
                self.dim = int(meta.get("dim", idx.d))
                self._index = idx
                return
            except Exception as exc:  # noqa: BLE001
                self._index_error = f"索引加载失败: {exc}"
        # 没有索引文件时**不要**预设维度：第一次 add_vectors 按实际向量维度建索引。
        # 以前这里写死 512，导致换成 mock(256) 或别的模型时直接抛「向量维度不一致」。
        self.dim = self.dim or 0
        self._index = None  # 懒建：第一次 add_vectors 时按维度创建

    def _ensure_index(self, dim: int):
        import faiss

        if self._index is None:
            self.dim = dim
            self._index = faiss.IndexIDMap2(faiss.IndexFlatIP(dim))
        elif self._index.d != dim:
            raise RuntimeError(
                f"向量维度不一致：索引是 {self._index.d} 维，本次是 {dim} 维。"
                "换 embedding 模型后请调 store.rebuild_index() 后重新入库，或删掉 data/index/ 重来。"
            )
        return self._index

    def add_vectors(self, vecs, ids: Sequence[int]) -> None:
        """vecs: (n, dim) 的归一化 float32；ids 与 chunks.chunk_id 对齐。"""
        import numpy as np

        if vecs is None or len(ids) == 0:
            return
        arr = np.ascontiguousarray(np.asarray(vecs, dtype="float32"))
        if arr.ndim == 1:
            arr = arr.reshape(1, -1)
        with self._lock:
            index = self._ensure_index(arr.shape[1])
            index.add_with_ids(arr, np.asarray(ids, dtype="int64"))
            self._index_dirty = True
        if config.AUTO_SAVE_INDEX:
            self.save_index()

    def search(self, vec, k: int = 8, doc_ids: Optional[Sequence[str]] = None,
               min_score: float = 0.0) -> List[Tuple[int, float]]:
        """返回 [(chunk_id, score)]。doc_ids 过滤采用「多召回再筛」，实现简单且够用。"""
        import numpy as np

        with self._lock:
            if self._index is None or self._index.ntotal == 0:
                return []
            q = np.asarray(vec, dtype="float32").reshape(1, -1)
            if q.shape[1] != self.dim:
                return []
            fetch = max(k * 4, k + 8) if doc_ids else k
            scores, ids = self._index.search(np.ascontiguousarray(q), min(fetch, self._index.ntotal))
        out: List[Tuple[int, float]] = []
        for sid, score in zip(ids[0], scores[0]):
            if sid < 0 or score < min_score:
                continue
            chunk = self.get_chunk(int(sid))
            if not chunk:
                continue
            if doc_ids and chunk["doc_id"] not in set(doc_ids):
                continue
            out.append((int(sid), float(score)))
            if len(out) >= k:
                break
        return out

    def remove_vectors(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        import numpy as np

        with self._lock:
            if self._index is None:
                return
            try:
                self._index.remove_ids(np.asarray(list(ids), dtype="int64"))
                self._index_dirty = True
            except Exception:  # noqa: BLE001
                pass

    def save_index(self) -> None:
        import faiss

        with self._lock:
            if self._index is None:
                return
            config.FAISS_PATH.parent.mkdir(parents=True, exist_ok=True)
            faiss.write_index(self._index, str(config.FAISS_PATH))
            config.FAISS_META_PATH.write_text(
                json.dumps({"dim": self.dim, "ntotal": int(self._index.ntotal),
                            "saved_at": time.time()}, ensure_ascii=False),
                encoding="utf-8",
            )
            self._index_dirty = False

    def rebuild_index(self) -> None:
        """丢弃 FAISS 文件并按当前 embedding 维度，由调用方重新灌向量。"""
        with self._lock:
            self._index = None
            for p in (config.FAISS_PATH, config.FAISS_META_PATH):
                if p.exists():
                    try:
                        os.remove(p)
                    except OSError:
                        pass

    def vector_count(self) -> int:
        with self._lock:
            return int(self._index.ntotal) if self._index is not None else 0

    def close(self) -> None:
        if config.AUTO_SAVE_INDEX and self._index_dirty:
            self.save_index()
        with self._lock:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass


_store: Optional[Store] = None
_store_lock = threading.Lock()


def get_store() -> Store:
    global _store
    with _store_lock:
        if _store is None:
            _store = Store()
    return _store
