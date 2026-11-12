"""多模态检索与问答（功能 3 的核心）。

一条提问的处理路线
------------------
    用户问题 + 用户图片
        │
        ├─(a) 有图？→ 百炼 qwen-vl 先给图生成一句话描述 → 拼进检索 query
        │            （bge-small-zh 是纯文本向量模型，图必须先「文字化」才能进同一向量空间）
        │
        ├─(b) Retriever：bge 向量化 query → FAISS 内积检索
        │            文本块 / 表格块 / **图片块（靠题注+描述被召回）** 一起参与排序
        │
        ├─(c) 组装上下文：给每个片段编号 [1][2]…，并把召回到的配图列出可用 URL
        │
        └─(d) 百炼生成：**同一个 user 消息**里放 上下文文本 + 用户原图 + 检索到的文档配图
                     流式吐字，同时要求「有用的地方原样插入 ![](/api/images/{id})」
                     → 前端 markdown 渲染 = 图文混排输出

所以「图文混排」有两处落地：
* **输入侧**：用户的图和问题一起进多模态模型（llm.build_messages 里同一个 content 数组）；
* **输出侧**：答案 markdown 内嵌文档配图，前端渲染成真正的图文混排。
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple, Union

import config
import llm
from embed import Embedder, get_embedder
from store import Store, get_store

ContextBudget = 6500   # 上下文字符预算（qwen-vl 上下文远大于此，留出图片 token 的余量）

SYSTEM_PROMPT = """你是企业知识库问答助手，回答必须**严格依据**下面提供的检索片段（来自用户上传并被解析的 PDF）。

工作方式
1. 先看片段再答：片段里没有的事实不要编；确属片段不足时，明确说明「知识库中未找到」，再简要给出通用理解。
2. 跨片段归纳：多个片段讲同一件事时合并表述，并标出来源编号，形如 〔1〕〔3〕。
3. **图文混排**：回答里凡讲到某张图所表达的内容，就在最贴近那句话的位置**原样插入**该图的 markdown
   （形如 `![描述](/api/images/12)`），不要改写 URL，不要虚构不存在的图片编号。图确实对回答没帮助时就不要插。
4. 用户上传了图片时：先看清图里在问什么（框出的区域、图上的文字、曲线走势都读出来），再结合知识库回答；
   如果用户的问题本身就围绕他给的这张图，优先解释这张图，再补充知识库中的对应出处。
5. 用中文、Markdown 结构（小标题 + 列表）输出，结论先行，不重复题目。
6. 最后用一行给出「引用：〔n〕对应的文件名与页码」。"""


# ---------------------------------------------------------------------------
@dataclass
class Hit:
    """一条检索命中（文本 / 表格 / 图片统一表示）。"""

    chunk_id: int
    doc_id: str
    filename: str
    page: int
    kind: str
    text: str
    score: float
    heading: str = ""
    image_id: Optional[int] = None
    image_url: str = ""
    image_caption: str = ""
    index: int = 0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index, "chunk_id": self.chunk_id, "doc_id": self.doc_id,
            "filename": self.filename, "page": self.page, "kind": self.kind,
            "score": round(self.score, 4), "heading": self.heading,
            "text": self.text[:1200], "image_id": self.image_id, "image_url": self.image_url,
            "caption": self.image_caption,
        }

    def citation(self) -> Dict[str, Any]:
        return {"index": self.index, "doc_id": self.doc_id, "filename": self.filename,
                "page": self.page, "kind": self.kind, "score": round(self.score, 4),
                "heading": self.heading, "image_url": self.image_url,
                "preview": (self.text or "")[:160]}


# ---------------------------------------------------------------------------
class Retriever:
    def __init__(self, store: Optional[Store] = None, embedder: Optional[Embedder] = None):
        self.store = store or get_store()
        self.embedder = embedder or get_embedder()

    def search(
        self,
        query_text: str,
        top_k: Optional[int] = None,
        doc_ids: Optional[Sequence[str]] = None,
        min_score: Optional[float] = None,
        kinds: Optional[Sequence[str]] = None,
    ) -> List[Hit]:
        """向量检索 -> 回表补全 -> 转成 Hit。"""
        top_k = top_k or config.RETRIEVAL_TOP_K
        min_score = config.RETRIEVAL_MIN_SCORE if min_score is None else min_score
        qvec = self.embedder.embed_query(query_text)
        pairs = self.store.search(qvec, k=top_k * 2, doc_ids=list(doc_ids) if doc_ids else None,
                                  min_score=min_score)
        hits: List[Hit] = []
        for chunk_id, score in pairs:
            row = self.store.get_chunk(chunk_id)
            if not row:
                continue
            if kinds and row["kind"] not in set(kinds):
                continue
            image_url, image_caption, image_id = "", "", row.get("image_id")
            if row["kind"] == "image" and image_id:
                image_url = f"/api/images/{image_id}"
                img = self.store.get_image(int(image_id))
                if img:
                    image_caption = img.get("caption") or ""
            heading = row.get("heading") or ""
            text = row["text"] or ""
            hits.append(Hit(
                chunk_id=chunk_id, doc_id=row["doc_id"], filename=row.get("filename") or row["doc_id"],
                page=int(row.get("page") or 0), kind=row["kind"], text=text, score=score,
                heading=heading, image_id=image_id, image_url=image_url,
                image_caption=image_caption,
            ))
            if len(hits) >= top_k:
                break
        for i, h in enumerate(hits, 1):
            h.index = i
        return hits

    def ensure_ready(self) -> Dict[str, Any]:
        n = self.store.vector_count()
        return {"vectors": n, "documents": self.store.counters(), "embedding": self.embedder.info()}


# ---------------------------------------------------------------------------
# query 侧的多模态改写：把用户图「文字化」后并入检索 query
# ---------------------------------------------------------------------------
def describe_user_images(images: Sequence[Dict[str, Any]], model: llm.LLM) -> List[str]:
    """images: [{"path"|"image_id"|"data_uri", "name"}] -> 图片描述列表（失败则空串）。"""
    out: List[str] = []
    if not images or model.mock:
        return out
    for im in images:
        src = _image_source(im)
        if not src:
            continue
        try:
            out.append(model.caption_image(src))
        except Exception:  # noqa: BLE001
            out.append("")
    return [o for o in out if o]


def _image_source(im: Dict[str, Any]) -> Union[str, Path]:
    if im.get("data_uri"):
        return str(im["data_uri"])
    if im.get("url"):
        return str(im["url"])
    if im.get("path"):
        return str(im["path"])
    if im.get("image_id"):
        p = get_store().image_abs_path(int(im["image_id"]))
        return str(p) if p else ""
    return ""


def build_search_query(question: str, captions: Sequence[str]) -> str:
    if not captions:
        return question
    joined = "；".join(c for c in captions if c)
    return f"{question}\n用户提供的图片内容：{joined}"


# ---------------------------------------------------------------------------
def build_context_block(hits: Sequence[Hit]) -> str:
    """检索片段 -> 喂给模型的上下文字符串（含可用图片 URL 清单）。"""
    if not hits:
        return "# 知识库检索结果\n（没有检索到任何片段：知识库可能还没有解析完成的文档，或问题与文档无关）"
    parts: List[str] = ["# 知识库检索结果（共 %d 条，编号〔n〕用于引用）" % len(hits), ""]
    used = 0
    for h in hits:
        body = h.text or ""
        if h.kind == "image":
            line = (f"〔{h.index}〕[图片块] 《{h.filename}》第{h.page}页 · 相似度{h.score:.3f}\n"
                    f"图片描述：{body or '（无描述）'}\n可用 markdown：![{h.image_caption or '配图'}]({h.image_url})")
        elif h.kind == "table":
            line = (f"〔{h.index}〕[表格] 《{h.filename}》第{h.page}页 · 相似度{h.score:.3f}\n{body}")
        else:
            line = (f"〔{h.index}〕[文本] 《{h.filename}》第{h.page}页 · 相似度{h.score:.3f}\n{body}")
        if used + len(line) > ContextBudget:
            parts.append(f"〔{h.index}〕…（超出上下文预算，已省略）")
            break
        used += len(line)
        parts.append(line)
        parts.append("")
    image_hits = [h for h in hits if h.kind == "image" and h.image_url]
    if image_hits:
        parts.append("# 可直接内嵌的配图（回答中如需展示，请**原样**使用下面的 markdown 链接）")
        for h in image_hits:
            parts.append(f"- ![{h.image_caption or '配图'}]({h.image_url})  ← 〔{h.index}〕"
                         f"《{h.filename}》第{h.page}页")
    return "\n".join(parts)


def history_messages(session_id: str) -> List[Dict[str, Any]]:
    """把 SQLite 里的历史轮次转成 messages（含历史里的用户图片）。"""
    store = get_store()
    turns = store.recent_qa(session_id, config.QA_HISTORY_TURNS)
    out: List[Dict[str, Any]] = []
    for t in turns:
        if t["role"] == "user":
            imgs = []
            for im in t.get("images") or []:
                src = _image_source(im) if isinstance(im, dict) else ""
                if src:
                    imgs.append(src)
            out.append({"role": "user", "content": t["content"], "images": imgs})
        elif t["role"] == "assistant":
            # 历史答案里内嵌的图片 URL 对模型没意义，去掉图片语法保留文字，省 token
            content = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t["content"] or "")
            out.append({"role": "assistant", "content": content.strip()})
    return out


# ---------------------------------------------------------------------------
# 主流程：产出事件流，worker.py 负责把事件发到 Kafka
# ---------------------------------------------------------------------------
def answer_events(
    request_id: str,
    session_id: str,
    question: str,
    images: Optional[Sequence[Dict[str, Any]]] = None,
    doc_ids: Optional[Sequence[str]] = None,
    top_k: Optional[int] = None,
) -> Iterator[Tuple[str, Dict[str, Any]]]:
    """生成 (kind, payload) 事件： stage / delta / final / error。

    做成 generator 是为了**边生成边发 Kafka**，前端才能打字机式看到答案。
    """
    images = list(images or [])
    t0 = time.time()
    store = get_store()
    model = llm.get_llm()
    retriever = Retriever(store=store)
    stats: Dict[str, Any] = {"request_id": request_id, "mock": model.mock}

    yield "stage", {"stage": "retrieving", "message": "向量化问题并检索知识库…",
                    "has_images": bool(images), "n_images": len(images)}

    try:
        captions: List[str] = []
        if images and not model.mock:
            # 图问一起送模型之前，先「文字化」用于检索（bge 是文本向量模型）
            yield "stage", {"stage": "retrieving", "message": f"先用视觉模型读取 {len(images)} 张用户上传图…"}
            captions = describe_user_images(images, model)
        search_query = build_search_query(question, captions)
        hits = retriever.search(search_query, top_k=top_k, doc_ids=doc_ids)

        # 文本命中里若没有图，但用户问题明显在问图，补一轮「只要图片块」的检索
        if not any(h.kind == "image" for h in hits):
            seen = {h.chunk_id for h in hits}
            extra = [h for h in retriever.search(
                search_query + " 图 示意图 流程图",
                top_k=max(2, (top_k or config.RETRIEVAL_TOP_K) // 2),
                doc_ids=doc_ids, kinds=["image"],
                min_score=config.RETRIEVAL_MIN_SCORE - 0.05,
            ) if h.chunk_id not in seen]
            if extra:
                hits = hits + extra
                for i, h in enumerate(hits, 1):
                    h.index = i
                stats["image_recall_extra"] = len(extra)

        stats.update({
            "retrieved": len(hits),
            "image_hits": sum(1 for h in hits if h.kind == "image"),
            "query_captions": captions,
            "top_score": round(hits[0].score, 4) if hits else 0.0,
            "retrieval_ms": int((time.time() - t0) * 1000),
        })
        yield "stage", {"stage": "retrieved", "message": f"检索到 {len(hits)} 个片段",
                        "hits": [h.as_dict() for h in hits], "stats": stats}

        context_block = build_context_block(hits)
        history = history_messages(session_id)

        # ---- 模型输入：用户图 + 检索到的文档配图，与问题同一个消息 ----
        doc_image_sources: List[str] = []
        for h in hits:
            if h.kind == "image" and h.image_id and len(doc_image_sources) < config.RETRIEVAL_MAX_IMAGES:
                p = store.image_abs_path(h.image_id)
                if p:
                    doc_image_sources.append(str(p))
        user_sources = [s for s in (_image_source(im) for im in images) if s]
        all_sources: List[Any] = list(user_sources) + list(doc_image_sources)
        stats["images_sent_to_model"] = len(all_sources)
        stats["user_images_sent"] = len(user_sources)
        stats["doc_images_sent"] = len(doc_image_sources)

        messages = llm.build_messages(
            system=SYSTEM_PROMPT,
            question=question if not captions else
                     f"{question}\n（系统提示：已用视觉模型读出你上传的图片内容为「{captions[0]}」，"
                     f"请结合图片本身作答）" if len(captions) == 1 else
                     f"{question}\n（系统提示：你上传的图片内容分别为 {json.dumps(captions, ensure_ascii=False)}）",
            images=all_sources,
            history=history,
            context_block=context_block,
        )
        use_model = model.pick_model(bool(all_sources))
        stats["model"] = use_model
        yield "stage", {"stage": "llm", "message": f"{use_model} 正在生成（{'图+问' if all_sources else '纯文本'}输入）…",
                        "mock": model.mock, "images_to_model": len(all_sources)}

        answer_parts: List[str] = []
        if model.mock:
            gen = model.stream(messages, model=use_model, mock_reply=list(
                llm.mock_reply_pieces(question, [h.as_dict() for h in hits],
                                      sum(1 for h in hits if h.kind == "image" and h.image_url))))
        else:
            try:
                gen = model.stream(messages, model=use_model)
            except llm.LLMError as exc:
                yield "error", {"error": str(exc), "stats": stats}
                return

        for piece in gen:
            if not piece:
                continue
            answer_parts.append(piece)
            yield "delta", {"delta": piece}

        answer = "".join(answer_parts).strip()
        # 把模型可能写的相对图片路径修正成 /api/images/{id}
        answer = normalize_image_links(answer, hits)
        citations = [h.citation() for h in hits]
        used_images = [
            {"image_id": h.image_id, "url": h.image_url, "caption": h.image_caption,
             "doc_id": h.doc_id, "filename": h.filename, "page": h.page, "index": h.index}
            for h in hits if h.kind == "image" and h.image_url
        ]
        stats["total_ms"] = int((time.time() - t0) * 1000)
        stats["answer_chars"] = len(answer)
        stats["usage"] = dict(model.usage)

        # 落库，供多轮上下文与页面历史
        store.add_qa_turn(session_id, "user", question, request_id=request_id,
                          images=[{"name": im.get("name", ""), "image_id": im.get("image_id"),
                                   "path": im.get("path", "")} for im in images])
        store.add_qa_turn(session_id, "assistant", answer, request_id=request_id,
                          citations=citations, mock=model.mock)

        yield "final", {"answer": answer, "citations": citations, "used_images": used_images,
                        "model": use_model, "mock": model.mock, "stats": stats}
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        yield "error", {"error": f"{type(exc).__name__}: {exc}", "stats": stats}


def normalize_image_links(answer: str, hits: Sequence[Hit]) -> str:
    """模型有时把图片写成 ![](/images/xx.png) 或漏了前导斜杠，这里统一纠正。

    规则：
    * 指向 /api/images/{id} 的原样保留；
    * 指向未知编号的图片（模型幻觉）整段删掉，避免前端出现碎图标；
    * ``img_ref``/文件名形式若能对上命中图片，替换成正确的 /api/images/{id}。
    """
    by_name: Dict[str, Hit] = {}
    for h in hits:
        if h.image_url and h.image_id:
            by_name[str(Path(h.image_url).name)] = h
            by_name[str(h.image_id)] = h

    def repl(m: re.Match) -> str:
        alt, url = m.group(1), m.group(2).strip()
        if url.startswith("/api/images/"):
            return m.group(0)
        cand = url.rsplit("/", 1)[-1]
        hit = by_name.get(cand) or by_name.get(cand.rsplit(".", 1)[0])
        if hit:
            return f"![{alt or hit.image_caption or '配图'}]({hit.image_url})"
        return ""   # 不认识的图片引用：删掉

    return re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", repl, answer)


def search_only(question: str, top_k: int = 8, doc_ids: Optional[Sequence[str]] = None) -> List[Dict[str, Any]]:
    """调试用：只看检索结果（/api/search）。"""
    return [h.as_dict() for h in Retriever().search(question, top_k=top_k, doc_ids=doc_ids)]
