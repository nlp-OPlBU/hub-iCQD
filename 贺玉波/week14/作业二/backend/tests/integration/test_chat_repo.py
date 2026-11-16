"""会话表读写：真实 MySQL（rag_test 库）。

覆盖：seq 分配、message_count、正序读取、摘要游标、删除。
"""

from __future__ import annotations

import pytest

from app.infra.db import repo_chat

pytestmark = pytest.mark.integration


def test_append_turn_assigns_increasing_seq(mysql_clean) -> None:
    session_id = "a" * 32
    assert repo_chat.append_turn(session_id, "第一问", "第一答") == 2
    assert repo_chat.append_turn(session_id, "第二问", "第二答") == 4

    messages = repo_chat.load_messages(session_id)
    assert [item["seq"] for item in messages] == [1, 2, 3, 4]
    assert [item["role"] for item in messages] == ["user", "assistant", "user", "assistant"]

    session = repo_chat.load_session(session_id)
    assert session is not None
    assert session["message_count"] == 4
    assert session["title"] == "第一问"


def test_append_turn_persists_citations(mysql_clean) -> None:
    session_id = "e" * 32
    citations = [{"index": 1, "doc_title": "讲义.pdf", "page_no": 3, "image_path": "/d/p003.png"}]
    repo_chat.append_turn(session_id, "问", "答", citations)

    messages = repo_chat.load_messages(session_id)
    # 引用只挂在助手消息上；用户消息没有快照
    assert messages[0]["citations"] == []
    assert messages[1]["citations"] == citations


def test_load_messages_returns_recent_in_order(mysql_clean) -> None:
    session_id = "b" * 32
    for index in range(3):
        repo_chat.append_turn(session_id, f"问{index}", f"答{index}")

    recent = repo_chat.load_messages(session_id, limit=2)
    assert [item["content"] for item in recent] == ["问2", "答2"]


def test_unsummarized_respects_cursor(mysql_clean) -> None:
    session_id = "c" * 32
    repo_chat.append_turn(session_id, "问1", "答1")
    repo_chat.append_turn(session_id, "问2", "答2")

    head = repo_chat.unsummarized(session_id, 0)
    assert [item["seq"] for item in head] == [1, 2, 3, 4]

    repo_chat.save_summary(session_id, "前两轮的摘要", 2)
    rest = repo_chat.unsummarized(session_id, 2)
    assert [item["seq"] for item in rest] == [3, 4]

    session = repo_chat.load_session(session_id)
    assert session is not None
    assert session["summary"] == "前两轮的摘要"
    assert session["summary_upto"] == 2


def test_clear_session_removes_rows(mysql_clean) -> None:
    session_id = "d" * 32
    repo_chat.append_turn(session_id, "问", "答")
    repo_chat.clear_session(session_id)

    assert repo_chat.load_messages(session_id) == []
    assert repo_chat.load_session(session_id) is None


def test_concurrent_append_keeps_seq_contiguous(mysql_clean) -> None:
    """同一会话并发写入靠 chat_session 行锁串行化，seq 不应重复或跳号。"""
    from concurrent.futures import ThreadPoolExecutor

    session_id = "e" * 32
    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(5):
            list(pool.map(lambda index: repo_chat.append_turn(session_id, f"问{index}", f"答{index}"), range(8)))

    messages = repo_chat.load_messages(session_id, limit=200)
    seqs = [item["seq"] for item in messages]
    assert seqs == list(range(1, 81))
    assert repo_chat.load_session(session_id)["message_count"] == 80
