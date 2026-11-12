"""问答服务：会话读写 + LangGraph 编排 + vLLM 生成。

会话数据的真源是 MySQL（``chat_session`` / ``chat_message``），Redis 只缓存最近若干轮：
- 读：先查缓存，未命中回落到数据库并回填
- 写：先落库再刷缓存；缓存失败不影响回答，只记日志
- 上下文：按 token 预算成对回溯历史，超出预算的早期对话由后台滚动摘要压缩

摘要放在回答之后异步做，不阻塞用户；摘要失败只降级为“按预算截断”，
不会影响本轮回答。
"""

from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import AppError, ErrorCode
from app.core.logging import get_logger, log_event
from app.core.tokens import estimate_tokens
from app.core.tracing import span
from app.graph import qa_graph
from app.infra.cache import redis_client
from app.infra.db import repo_chat
from app.infra.model import vision, vllm_client

logger = get_logger("app.services.chat")

SUMMARY_PROMPT = (
    "把下面的多轮对话压缩成一段不超过 400 字的中文摘要，供后续问答作为背景。"
    "保留：用户关心的主题、已经确认的结论、仍未解决的点。"
    "只输出摘要正文，不要加标题、不要编造对话里没有的信息。"
)

# 短问题常常「它/这个/上面说的」满天飞，向量检索抓不住指代；先补全再检索
REWRITE_PROMPT = (
    "把用户的问题改写成一句适合在文档库里检索的中文关键词句。要求："
    "补全指代（它、这个、上面说的、那个方法）；保留专有名词与英文缩写；"
    "不要回答问题，不要解释，不要加引号，只输出改写结果。"
)
REWRITE_MAX_OUTPUT = 60
REWRITE_NOISE = re.compile(r"[「」“”\"'\n\r]")

# 后台摘要任务需要持有引用，否则可能被 GC
_background: set[asyncio.Task] = set()


def new_session_id() -> str:
    return uuid.uuid4().hex


def _map_llm_error(exc: BaseException) -> AppError:
    if isinstance(exc, httpx.TimeoutException):
        return AppError(ErrorCode.MODEL_TIMEOUT, "生成超时，请稍后重试或缩短问题")
    return AppError(ErrorCode.MODEL_UNAVAILABLE, f"生成服务不可用: {exc}")


async def _rewrite_query(question: str, history: list[dict[str, Any]] | None = None) -> str | None:
    """短问题条件式改写：只换检索用的 query，生成仍用用户原话。

    改写失败、超时、结果和原问题一样都返回 None，调用方沿用原问题。
    ``history`` 只在有上一轮时传入：没有上下文的话，「它的缺点呢」这类指代根本无从补全。
    """
    settings = get_settings().retrieval
    text = question.strip()
    if not settings.rewrite_enabled or len(text) > settings.rewrite_max_chars:
        return None
    if not qa_graph.needs_retrieval(text):
        return None
    payload = text
    if history:
        recent = "\n".join(
            f"{item.get('role')}：{str(item.get('content') or '')[:120]}" for item in history[-4:]
        )
        payload = f"【最近对话】\n{recent}\n\n【本轮问题】\n{text}"
    try:
        with span("chat.rewrite", question_len=len(text), with_history=bool(history)):
            # chat_completion 内部会对 5xx/超时重试；这里用总预算卡死，避免改写拖慢提问
            async with asyncio.timeout(settings.rewrite_timeout):
                rewritten = await vllm_client.chat_completion(
                    [{"role": "system", "content": REWRITE_PROMPT}, {"role": "user", "content": payload}],
                    max_tokens=80,
                    temperature=0.0,
                    timeout=settings.rewrite_timeout,
                )
    except Exception as exc:  # 改写是锦上添花，失败直接用原问题
        log_event(logger, "chat_rewrite_failed", level=30, error=str(exc))
        return None
    cleaned = REWRITE_NOISE.sub("", (rewritten or "")).strip()[:REWRITE_MAX_OUTPUT]
    return cleaned if cleaned and cleaned != text else None


def _sanitize_answer(answer: str, citations: list[dict[str, Any]]) -> str:
    """删掉越界引用编号。提示词已经给了编号上界，这里只是兜底。"""
    if not citations:
        return answer
    return qa_graph.sanitize_refs(answer, len(citations))


async def _image_parts(citations: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把命中图片按【图N】编号编码，供生成侧插到对应资料后面。

    单张失败只跳过这一张，不影响文本问答；编号取引用里的 image_index，
    和上下文里的【图N】标记严格对应。
    """
    limit = get_settings().vllm.max_images
    if limit <= 0:
        return []
    picked = [item for item in citations if item.get("image_path")][:limit]
    if not picked:
        return []

    def _encode(items: list[dict[str, Any]]) -> list[dict[str, str]]:
        parts: list[dict[str, str]] = []
        for item in items:
            data = vision.to_data_url(str(item["image_path"]))
            if not data:
                continue
            title = item.get("doc_title") or str(item.get("doc_id") or "")[:8]
            page = item.get("page_no") or "?"
            number = item.get("image_index") or len(parts) + 1
            parts.append({"url": data, "label": f"【图{number}｜《{title}》第 {page} 页】"})
        return parts

    return await asyncio.to_thread(_encode, picked)


async def _load_context(session_id: str) -> tuple[list[dict[str, Any]], str | None]:
    """返回 (最近历史, 滚动摘要)：缓存优先，未命中回落数据库并回填。"""
    messages = await asyncio.to_thread(redis_client.read_chat_messages, session_id)
    if not messages:
        messages = await asyncio.to_thread(repo_chat.load_messages, session_id, 40)
        if messages:
            await asyncio.to_thread(redis_client.replace_chat_messages, session_id, messages)
    session = await asyncio.to_thread(repo_chat.load_session, session_id)
    summary = (session or {}).get("summary")
    return messages, summary


async def ask(
    question: str,
    *,
    session_id: str | None = None,
    top_k: int | None = None,
    doc_id: str | None = None,
    with_images: bool = True,
    image: str | None = None,
) -> dict[str, Any]:
    session_id = session_id or new_session_id()
    history, summary = await _load_context(session_id)
    citations: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    need_retrieval = False
    retrieval_query: str | None = None

    if image:
        # 用户自己带了图：直接看图作答，不混入知识库资料，避免两类图片互相干扰
        messages = qa_graph.build_messages(
            question, "", history, summary=summary, has_context=False, user_image=image
        )
        answer = await _complete(messages, hits=0)
    else:
        retrieval_query = await _rewrite_query(question, history)
        state = await qa_graph.prepare(
            question,
            retrieval_query=retrieval_query,
            history=history,
            top_k=top_k,
            doc_id=doc_id,
            with_images=with_images,
        )
        citations = state.get("citations") or []
        hits = state.get("hits") or []
        need_retrieval = bool(state.get("need_retrieval"))

        if not need_retrieval:
            # 问候语：不查库，直接让模型应答
            messages = qa_graph.build_messages(question, "", history, summary=summary, has_context=False)
            answer = await _complete(messages, hits=0)
        elif not hits:
            # 知识库里没命中不等于不回答：让模型用通用知识兜底，并明确标注来源边界
            log_event(logger, "chat_no_hits_fallback", session_id=session_id)
            messages = qa_graph.build_messages(
                question, "", history, summary=summary, has_context=False, no_material=True
            )
            answer = await _complete(messages, hits=0)
        else:
            images = await _image_parts(citations)
            messages = qa_graph.build_messages(
                question,
                state.get("context", ""),
                history,
                summary=summary,
                images=images,
                max_ref=len(citations),
            )
            answer = await _complete(messages, hits=len(hits))
        answer = _sanitize_answer(answer, citations)

    await _persist(session_id, question or "（图片提问）", answer, citations)
    return {
        "session_id": session_id,
        "answer": answer,
        "citations": citations,
        "hits": hits,
        "need_retrieval": need_retrieval,
        "retrieval_query": retrieval_query or question,
    }


async def _complete(messages: list[dict[str, Any]], *, hits: int) -> str:
    try:
        with span("qa.generate", hits=hits):
            return await vllm_client.chat_completion(messages)
    except Exception as exc:
        raise _map_llm_error(exc) from exc


async def ask_stream(
    question: str,
    *,
    session_id: str | None = None,
    top_k: int | None = None,
    doc_id: str | None = None,
    with_images: bool = True,
    image: str | None = None,
) -> AsyncIterator[tuple[str, Any]]:
    """产出 (事件名, 数据)：meta → delta… → done / error。"""
    session_id = session_id or new_session_id()
    history, summary = await _load_context(session_id)
    citations: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    need_retrieval = False
    retrieval_query: str | None = None

    if image:
        messages = qa_graph.build_messages(
            question, "", history, summary=summary, has_context=False, user_image=image
        )
    else:
        retrieval_query = await _rewrite_query(question, history)
        try:
            state = await qa_graph.prepare(
                question,
                retrieval_query=retrieval_query,
                history=history,
                top_k=top_k,
                doc_id=doc_id,
                with_images=with_images,
            )
        except Exception as exc:
            yield "error", {"code": ErrorCode.INTERNAL.value, "message": f"检索失败: {exc}"}
            return

        citations = state.get("citations") or []
        hits = state.get("hits") or []
        need_retrieval = bool(state.get("need_retrieval"))

        if need_retrieval and not hits:
            # 没命中也要给答案：走通用知识兜底，明确标注「不来自知识库」
            messages = qa_graph.build_messages(
                question, "", history, summary=summary, has_context=False, no_material=True
            )
            log_event(logger, "chat_no_hits_fallback", session_id=session_id)
        elif need_retrieval:
            images = await _image_parts(citations)
            messages = qa_graph.build_messages(
                question,
                state.get("context", ""),
                history,
                summary=summary,
                images=images,
                max_ref=len(citations),
            )
        else:
            messages = qa_graph.build_messages(
                question, "", history, summary=summary, has_context=False
            )

    yield "meta", {
        "session_id": session_id,
        "citations": citations,
        "hit_count": len(hits),
        "need_retrieval": need_retrieval,
        "retrieval_query": retrieval_query or question,
    }

    pieces: list[str] = []
    persisted = False  # 已把本轮落库；用于区分“正常结束”和“客户端中途断流”
    try:
        with span("qa.generate", hits=len(hits), stream=True):
            async for piece in vllm_client.stream_chat(messages):
                pieces.append(piece)
                yield "delta", piece
        answer = _sanitize_answer("".join(pieces), citations)
        await _persist(session_id, question or "（图片提问）", answer, citations)
        persisted = True
        yield "done", {"session_id": session_id, "chars": len(answer), "answer": answer}
    except Exception as exc:
        log_event(logger, "chat_stream_failed", level=30, session_id=session_id, error=str(exc))
        if pieces:  # 已经吐出一部分就落库，避免会话里出现“只有问题没有回答”
            await _persist(
                session_id,
                question or "（图片提问）",
                _sanitize_answer("".join(pieces), citations),
                citations,
            )
            persisted = True
        yield "error", {"code": ErrorCode.MODEL_UNAVAILABLE.value, "message": f"生成中断: {exc}"}
    finally:
        # 客户端主动断开（GeneratorExit）或请求被取消（CancelledError）都不会走上面的分支：
        # 生成器已产出部分内容时，把这段“半截回答”交给后台任务落库，避免丢失。
        # 这里只能用 create_task，finally 中 await 会让 aclose() 卡住。
        if not persisted and pieces:
            try:
                asyncio.create_task(
                    _persist(
                        session_id,
                        question or "（图片提问）",
                        _sanitize_answer("".join(pieces), citations),
                        citations,
                    )
                )
            except RuntimeError:  # 事件循环已关闭，只能放弃
                pass


async def _persist(
    session_id: str,
    question: str,
    answer: str,
    citations: list[dict[str, Any]] | None = None,
) -> None:
    """先落库（真源），再刷缓存并触发滚动摘要；任何一步失败都不应吞掉本轮回答。

    引用快照随助手消息一起落库，历史回放才能还原来源卡片和图片。
    """
    try:
        await asyncio.to_thread(repo_chat.append_turn, session_id, question, answer, citations)
    except Exception as exc:
        log_event(
            logger, "chat_persist_failed", level=40, session_id=session_id, error=str(exc)
        )
        return

    try:
        recent = await asyncio.to_thread(repo_chat.load_messages, session_id, 20)
        await asyncio.to_thread(redis_client.replace_chat_messages, session_id, recent)
    except Exception as exc:
        log_event(logger, "chat_cache_failed", level=30, session_id=session_id, error=str(exc))

    task = asyncio.create_task(_maybe_summarize(session_id))
    _background.add(task)
    task.add_done_callback(_background.discard)


async def _maybe_summarize(session_id: str) -> None:
    """未摘要的历史超过阈值时，把除最近 N 条以外的部分压成摘要。"""
    settings = get_settings().chat
    try:
        session = await asyncio.to_thread(repo_chat.load_session, session_id)
        if not session:
            return
        pending = await asyncio.to_thread(
            repo_chat.unsummarized, session_id, int(session["summary_upto"])
        )
        if len(pending) <= settings.summary_keep_messages:
            return
        head = pending[: -settings.summary_keep_messages]
        if sum(estimate_tokens(item["content"]) for item in head) < settings.summary_trigger_tokens:
            return

        transcript = "\n".join(
            f"{'用户' if item['role'] == 'user' else '助手'}：{item['content']}" for item in head
        )
        previous = (session.get("summary") or "").strip()
        if previous:
            transcript = f"【已有摘要】\n{previous}\n\n【新增对话】\n{transcript}"

        with span("chat.summarize", messages=len(head)):
            summary = await vllm_client.chat_completion(
                [{"role": "system", "content": SUMMARY_PROMPT}, {"role": "user", "content": transcript}],
                max_tokens=400,  # 摘要固定上限，避免模型把早期对话又完整复述一遍
                temperature=0.1,
            )
        await asyncio.to_thread(
            repo_chat.save_summary, session_id, summary.strip()[: settings.summary_max_chars], head[-1]["seq"]
        )
        log_event(logger, "chat_summarized", session_id=session_id, upto=head[-1]["seq"])
    except Exception as exc:  # 摘要失败只是退化为“按 token 预算截断”
        log_event(logger, "chat_summarize_failed", level=30, session_id=session_id, error=str(exc))


async def history(session_id: str) -> list[dict[str, Any]]:
    return await asyncio.to_thread(repo_chat.load_messages, session_id, 40)


async def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    return await asyncio.to_thread(repo_chat.list_sessions, limit)


async def reset(session_id: str) -> None:
    await asyncio.to_thread(repo_chat.clear_session, session_id)
    await asyncio.to_thread(redis_client.clear_chat_session, session_id)
