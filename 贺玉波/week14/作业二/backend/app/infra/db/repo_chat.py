"""chat_session / chat_message 读写。

MySQL 是会话真源，Redis 只做最近若干轮的热缓存：
- 写：先落库（失败则整轮会话丢失，必须让调用方知道），再刷缓存（失败只记日志）
- 读：优先缓存，未命中回落数据库并回填

同一会话并发提问靠 ``chat_session`` 行锁串行化：
seq 由锁内 ``message_count`` 递增分配，保证消息严格有序、摘要游标不会重复。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, insert, select
from sqlalchemy import text as sql_text

from app.core.tokens import estimate_tokens
from app.infra.db.engine import get_engine
from app.infra.db.models import ChatMessage, ChatSession


def append_turn(
    session_id: str,
    question: str,
    answer: str,
    citations: list[dict[str, Any]] | None = None,
) -> int:
    """追加一问一答，返回写完后会话内的消息条数。

    ``citations`` 只挂在助手这条消息上：它记录本轮引用/图片快照，
    历史回放时前端据此还原来源卡片，不必重新检索。
    """
    rows = [
        {
            "session_id": session_id,
            "role": "user",
            "content": question,
            "citations": None,
            "tokens": estimate_tokens(question),
        },
        {
            "session_id": session_id,
            "role": "assistant",
            "content": answer,
            "citations": citations or None,
            "tokens": estimate_tokens(answer),
        },
    ]
    # 建会话单独提交：若与 SELECT ... FOR UPDATE 放在同一事务，
    # INSERT IGNORE 持有记录锁后升级为排他锁，并发时会互相等待形成死锁。
    with get_engine().begin() as conn:
        conn.execute(
            sql_text("INSERT IGNORE INTO chat_session (id, title) VALUES (:id, :title)"),
            {"id": session_id, "title": question.strip()[:64] or None},
        )

    with get_engine().begin() as conn:
        locked = conn.execute(
            sql_text("SELECT message_count FROM chat_session WHERE id = :id FOR UPDATE"),
            {"id": session_id},
        ).first()
        base = int(locked[0]) if locked else 0
        for offset, row in enumerate(rows, start=1):
            row["seq"] = base + offset
        conn.execute(insert(ChatMessage), rows)
        conn.execute(
            sql_text("UPDATE chat_session SET message_count = :count WHERE id = :id"),
            {"count": base + len(rows), "id": session_id},
        )
    return base + len(rows)


def load_messages(session_id: str, limit: int = 40) -> list[dict[str, Any]]:
    """按时间正序返回最近 limit 条消息（含助手回答的引用快照）。"""
    stmt = (
        select(
            ChatMessage.seq,
            ChatMessage.role,
            ChatMessage.content,
            ChatMessage.citations,
            ChatMessage.tokens,
        )
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.seq.desc())
        .limit(limit)
    )
    with get_engine().begin() as conn:
        rows = conn.execute(stmt).mappings().all()
    messages: list[dict[str, Any]] = []
    for row in reversed(rows):
        item = dict(row)
        item["citations"] = item.get("citations") or []
        messages.append(item)
    return messages


def load_session(session_id: str) -> dict[str, Any] | None:
    stmt = select(
        ChatSession.id,
        ChatSession.title,
        ChatSession.summary,
        ChatSession.summary_upto,
        ChatSession.message_count,
    ).where(ChatSession.id == session_id)
    with get_engine().begin() as conn:
        row = conn.execute(stmt).mappings().first()
    return dict(row) if row else None


def list_sessions(limit: int = 30) -> list[dict[str, Any]]:
    """最近活跃的会话列表，供前端「历史会话」侧栏。"""
    stmt = (
        select(ChatSession.id, ChatSession.title, ChatSession.message_count, ChatSession.updated_at)
        .where(ChatSession.message_count > 0)
        .order_by(ChatSession.updated_at.desc(), ChatSession.id.desc())
        .limit(limit)
    )
    with get_engine().begin() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [
        {
            "session_id": row["id"],
            "title": row["title"] or "（未命名会话）",
            "message_count": int(row["message_count"]),
            "updated_at": row["updated_at"].isoformat(timespec="seconds"),
        }
        for row in rows
    ]


def unsummarized(session_id: str, upto: int) -> list[dict[str, Any]]:
    """返回 summary_upto 之后、尚未被摘要覆盖的消息。"""
    stmt = (
        select(ChatMessage.seq, ChatMessage.role, ChatMessage.content)
        .where(ChatMessage.session_id == session_id, ChatMessage.seq > upto)
        .order_by(ChatMessage.seq)
    )
    with get_engine().begin() as conn:
        rows = conn.execute(stmt).mappings().all()
    return [dict(row) for row in rows]


def save_summary(session_id: str, summary: str, upto: int) -> None:
    with get_engine().begin() as conn:
        conn.execute(
            sql_text(
                "UPDATE chat_session SET summary = :summary, summary_upto = :upto WHERE id = :id"
            ),
            {"summary": summary, "upto": upto, "id": session_id},
        )


def exists(session_id: str) -> bool:
    with get_engine().begin() as conn:
        row = conn.execute(
            sql_text("SELECT 1 FROM chat_session WHERE id = :id"), {"id": session_id}
        ).first()
    return row is not None


def clear_session(session_id: str) -> None:
    with get_engine().begin() as conn:
        conn.execute(delete(ChatMessage).where(ChatMessage.session_id == session_id))
        conn.execute(delete(ChatSession).where(ChatSession.id == session_id))
