"""LangGraph 问答编排。

图负责三件事：判断是否需要检索 → 检索（可跳过）→ 组织带编号的上下文。
生成不放进图里：流式输出需要逐 token 下发，放在 API 层直接调 vLLM 更直接，
图的状态（context / citations / hits）作为生成的输入。
"""

from __future__ import annotations

import asyncio
import re
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import get_settings
from app.core.logging import get_logger, log_event
from app.core.tokens import estimate_tokens
from app.core.tracing import span
from app.services import retrieval_service

logger = get_logger("app.graph.qa")

GREETINGS = ("你好", "您好", "hi", "hello", "嗨", "在吗", "谢谢", "thanks")
CONTEXT_BUDGET = 4000
CITATION_SNIPPET = 120
# 同一页的所有 chunk 折成一条，正文拼接上限；避免一页被切碎后反复占上下文
MAX_PAGE_CHARS = 900

# MinerU flash 的正文里会残留内部图片占位引用与页码注释，不能直接喂给模型或前端展示
DOC_REF = re.compile(r"!\[[^\]]*\]\(doc:[^)]*\)")
PAGE_MARK = re.compile(r"<!--\s*page\s+\d+(?:\s*of\s+\d+)?\s*-->", re.IGNORECASE)
SPACES = re.compile(r"\s+")

SYSTEM_PROMPT = """你是一个知识库问答助手。回答要有用、可核查，不要只会复述资料。

回答方式：
1. 资料里有的内容，优先用资料回答，并在句末用方括号标注出处，例如 [1]、[2][3]。
   编号只能取自本轮【资料】里出现过的条目。
2. 资料没覆盖、但对回答这个问题有帮助的内容，用你自己的通用知识补充。
   补充部分另起一段，以「补充（非资料内容）：」开头，这段不要标注 [N] 编号。
3. 如果资料与问题关系不大，先说明「资料中未直接涉及」，再给出你的通用解释和可行建议，
   不要只用一句“资料未提及”就结束回答。
4. 不要编造具体的数字、日期、文献名、人名或出处；不确定的地方直接说明不确定。
   资料与你的通用知识冲突时，以资料为准，并指出冲突点。

其他要求：
- 【资料】里的图片按【图1】【图2】标号，紧跟在对应资料后面给出。提到某张图时写明【图N】、
  所属文档名和页码；只描述你确实看得清的内容，看不清或与问题无关就不要硬加描述。
- 如果用户本轮上传了自己的图片，先如实描述图中可见内容，再结合资料回答。
- 同一页的资料只写一次，不要重复复述同一段内容；回答使用简体中文，条理清晰，不要复述问题。"""

# 检索为空时追加的说明：允许模型用自己的知识兜底，但仍要与资料引用区分开
NO_MATERIAL_NOTE = (
    "本轮知识库没有检索到相关资料。请直接用你的通用知识回答，"
    "开头注明「以下内容不来自知识库」，并说明知识库中暂无对应内容；"
    "不要编造引用编号，也不要虚构文档名或页码。"
)


class QAState(TypedDict, total=False):
    question: str
    retrieval_query: str
    history: list[dict[str, Any]]
    top_k: int | None
    doc_id: str | None
    with_images: bool
    need_retrieval: bool
    hits: list[dict[str, Any]]
    context: str
    citations: list[dict[str, Any]]


# 回答里引用的编号；生成模型偶尔会编出 [9]，需要校验再清理
REF = re.compile(r"\[(\d+)\]")


# ---------------------------------------------------------------- 纯函数（便于单测）


def needs_retrieval(question: str) -> bool:
    """短问候语不查库，省一次向量化与检索。"""
    text = question.strip().lower()
    if len(text) <= 2:
        return False
    return not any(text == word or (len(text) <= 6 and word in text) for word in GREETINGS)


def strip_refs(text: str) -> str:
    """去掉 MinerU 的内部引用标记，保留正文换行（喂模型用）。"""
    cleaned = DOC_REF.sub(" ", PAGE_MARK.sub(" ", text or ""))
    return "\n".join(line.rstrip() for line in cleaned.splitlines()).strip()


def clean_snippet(text: str) -> str:
    """引用列表展示用：压成一行、限制长度。"""
    return SPACES.sub(" ", strip_refs(text))[:CITATION_SNIPPET]


def citation_numbers(answer: str) -> list[int]:
    return [int(value) for value in REF.findall(answer or "")]


def invalid_refs(answer: str, max_index: int) -> list[int]:
    """返回越界编号。只做校验不做重写：多花一次生成的钱，收益还不如提示词约束。"""
    return sorted({value for value in citation_numbers(answer) if value < 1 or value > max_index})


def sanitize_refs(answer: str, max_index: int) -> str:
    """把越界编号的方括号去掉、正文保留，避免用户看到一个并不存在的来源。"""
    return REF.sub(
        lambda match: match.group(0) if 1 <= int(match.group(1)) <= max_index else "",
        answer or "",
    )


def collapse_hits(hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把同一页的多个命中折成一条：正文去重拼接，图片只留第一张。

    向量库里一页会被切成若干 chunk，RRF 后同页条目会连续挤满上下文，
    模型就把同一段话反复讲。折到「文档 + 页码」这一粒度后，上下文更干净，
    引用也正好对应前端「第几页、哪张图」的展示方式。
    """
    merged: dict[tuple[str, int], dict[str, Any]] = {}
    order: list[tuple[str, int]] = []
    for hit in hits:
        key = (str(hit.get("doc_id") or ""), int(hit.get("page_no") or 0))
        entry = merged.get(key)
        if entry is None:
            entry = {
                "doc_id": key[0],
                "page_no": key[1],
                "doc_title": hit.get("doc_title") or "",
                "text_parts": [],
                "image_path": None,
                "chunk_id": "",
                "score": 0.0,
                "sources": [],
            }
            merged[key] = entry
            order.append(key)
        entry["score"] = max(entry["score"], float(hit.get("score") or 0.0))
        if not entry["doc_title"]:
            entry["doc_title"] = hit.get("doc_title") or ""
        for source in hit.get("sources") or []:
            if source not in entry["sources"]:
                entry["sources"].append(source)

    for hit in hits:
        key = (str(hit.get("doc_id") or ""), int(hit.get("page_no") or 0))
        entry = merged[key]
        body = strip_refs(hit.get("text") or "")
        if body and body not in entry["text_parts"] and len("".join(entry["text_parts"])) < MAX_PAGE_CHARS:
            entry["text_parts"].append(body)
            entry["chunk_id"] = entry["chunk_id"] or str(hit.get("chunk_id") or "")
        image = hit.get("image_path")
        if image and not entry["image_path"]:
            entry["image_path"] = str(image)
            entry["chunk_id"] = entry["chunk_id"] or str(hit.get("chunk_id") or "")

    collapsed: list[dict[str, Any]] = []
    for key in order:
        entry = merged[key]
        entry["text"] = "\n".join(entry.pop("text_parts"))[:MAX_PAGE_CHARS]
        collapsed.append(entry)
    return collapsed


def build_context(
    hits: list[dict[str, Any]],
    budget: int = CONTEXT_BUDGET,
) -> tuple[str, list[dict[str, Any]]]:
    """把命中结果拼成带编号的上下文，并生成引用列表。

    先按页折叠，再逐页编号；图片命中会在条目里写【图N】：生成侧按这个编号
    把图片插在对应位置，模型才分得清哪张图属于哪份文档哪一页。
    """
    blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    used = 0
    image_no = 0
    for index, hit in enumerate(collapse_hits(hits), start=1):
        page = hit.get("page_no") or "?"
        title = hit.get("doc_title") or hit.get("doc_id", "")[:8]
        image_path = hit.get("image_path")
        image_index = 0
        if image_path:
            image_no += 1
            image_index = image_no
        body = strip_refs(hit.get("text") or "")
        if not body and image_path:
            body = f"（本条目是一条图片资料，内容见下方【图{image_index}】）"
        if not body:
            continue
        header = f"[{index}] 《{title}》第 {page} 页"
        if image_index:
            header += f" · 图片【图{image_index}】"
        block = f"{header}\n{body}"
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
        citations.append(
            {
                "index": index,
                "doc_id": hit.get("doc_id") or "",
                "doc_title": hit.get("doc_title") or "",
                "chunk_id": hit.get("chunk_id") or "",
                "page_no": int(hit.get("page_no") or 0),
                "score": round(float(hit.get("score") or 0.0), 6),
                "sources": hit.get("sources") or [],
                "image_path": image_path,
                "image_index": image_index,
                "snippet": clean_snippet(hit.get("text") or ""),
            }
        )
    return "\n\n".join(blocks), citations


def build_messages(
    question: str,
    context: str,
    history: list[dict[str, Any]] | None = None,
    *,
    summary: str | None = None,
    has_context: bool = True,
    history_budget: int | None = None,
    images: list[dict[str, str]] | None = None,
    user_image: str | None = None,
    max_ref: int | None = None,
    no_material: bool = False,
) -> list[dict[str, Any]]:
    """系统提示 + 滚动摘要 + 最近若干轮历史 + 本轮输入。

    历史按 **token 预算** 从新到旧成对回溯，而不是固定条数：
    一轮长回答就可能吃掉整个窗口，按条数裁剪会直接把 prompt 顶爆。

    ``images`` 是检索到的资料图片，每项 ``{"url": data_url, "label": "【图N｜《文档》第X页】"}``。
    图片按 label 紧跟在资料文本后插入，问题放最后——不能把图全堆在末尾，否则模型
    无法把某张图和某段正文对上号。``user_image`` 是用户本轮粘贴/上传的图。

    ``max_ref`` 是本轮资料条数：明确告诉模型编号上界，比事后删掉越界引用更省一次生成。
    """
    budget = history_budget or get_settings().chat.history_token_budget
    messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if summary:
        messages.append({"role": "system", "content": f"【对话摘要】\n{summary}"})
    if has_context and max_ref:
        messages.append(
            {
                "role": "system",
                "content": f"本轮【资料】共 {max_ref} 条，引用编号只能使用 [1] 到 [{max_ref}]，不要出现更大的编号。",
            }
        )
    if no_material:
        messages.append({"role": "system", "content": NO_MATERIAL_NOTE})
    messages.extend(_recent_turns(history or [], budget))

    if not images and not user_image:
        text = f"【资料】\n{context}\n\n【问题】\n{question}" if has_context else question
        messages.append({"role": "user", "content": text})
        return messages

    parts: list[dict[str, Any]] = []
    if has_context and context:
        parts.append({"type": "text", "text": f"【资料】\n{context}"})
    for picture in images or []:
        if picture.get("label"):
            parts.append({"type": "text", "text": picture["label"]})
        parts.append({"type": "image_url", "image_url": {"url": picture["url"]}})
    if user_image:
        parts.append({"type": "text", "text": "【用户本轮上传的图片】"})
        parts.append({"type": "image_url", "image_url": {"url": user_image}})
    prompt = f"【问题】\n{question}" if question else "【问题】\n请描述并分析这张图片。"
    parts.append({"type": "text", "text": prompt})
    messages.append({"role": "user", "content": parts})
    return messages


def _recent_turns(history: list[dict[str, Any]], budget: int) -> list[dict[str, Any]]:
    """把历史切成“轮”（user + assistant），从最新一轮往回装到预算用尽。"""
    turns: list[list[dict[str, Any]]] = []
    for item in history:
        role = item.get("role")
        content = item.get("content")
        if role not in ("user", "assistant") or not content:
            continue
        clean = {"role": role, "content": content}
        if role == "user" or not turns:
            turns.append([clean])
        else:
            turns[-1].append(clean)

    picked: list[list[dict[str, Any]]] = []
    used = 0
    for turn in reversed(turns):
        cost = sum(estimate_tokens(item["content"]) for item in turn)
        if used + cost > budget:
            break
        picked.append(turn)
        used += cost

    return [item for turn in reversed(picked) for item in turn]


# ---------------------------------------------------------------- 图节点


async def _route(state: QAState) -> dict[str, Any]:
    return {"need_retrieval": needs_retrieval(state["question"])}


async def _retrieve(state: QAState) -> dict[str, Any]:
    # 检索用改写后的 query（短问题补全指代词），生成仍用用户原话
    query = state.get("retrieval_query") or state["question"]
    with span("qa.retrieve", question_len=len(query)):
        hits, took_ms = await asyncio.to_thread(
            retrieval_service.search,
            query,
            state.get("top_k"),
            with_images=state.get("with_images", True),
            doc_id=state.get("doc_id"),
        )
    log_event(logger, "qa_retrieve", hits=len(hits), took_ms=took_ms)
    return {"hits": hits}


async def _context(state: QAState) -> dict[str, Any]:
    context, citations = build_context(state.get("hits") or [])
    return {"context": context, "citations": citations}


def _after_route(state: QAState) -> str:
    return "retrieve" if state.get("need_retrieval") else "context"


@lru_cache(maxsize=1)
def _compiled():
    graph = StateGraph(QAState)
    graph.add_node("route", _route)
    graph.add_node("retrieve", _retrieve)
    graph.add_node("context", _context)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", _after_route, {"retrieve": "retrieve", "context": "context"})
    graph.add_edge("retrieve", "context")
    graph.add_edge("context", END)
    return graph.compile()


async def prepare(
    question: str,
    *,
    retrieval_query: str | None = None,
    history: list[dict[str, Any]] | None = None,
    top_k: int | None = None,
    doc_id: str | None = None,
    with_images: bool = True,
) -> QAState:
    """跑完检索与上下文组装，返回可直接用于生成的状态。

    ``top_k`` 留空时由检索层按 ``final_top_k`` 决定，避免这里另算一套默认值。
    """
    state: QAState = {
        "question": question,
        "retrieval_query": retrieval_query or question,
        "history": history or [],
        "top_k": top_k,
        "doc_id": doc_id,
        "with_images": with_images,
    }
    return await _compiled().ainvoke(state)
