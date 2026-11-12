"""滚动摘要：真实 MySQL，模型边界替换。

验证三条不变式：
1. 未摘要历史超过阈值时，把早期部分压成摘要并推进游标
2. 最近 N 条原文不动，仍可逐字回放
3. 已经摘要过的不再重复调用模型
"""

from __future__ import annotations

import pytest

from app.infra.db import repo_chat
from app.services import chat_service

pytestmark = pytest.mark.integration


@pytest.fixture()
def fake_summary(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    async def completion(messages, **kwargs):
        calls.append(messages[-1]["content"])
        return "用户在反复确认多模态检索的实现细节。"

    monkeypatch.setattr(chat_service.vllm_client, "chat_completion", completion)
    return calls


async def test_summary_compresses_old_turns(mysql_clean, fake_summary) -> None:
    session_id = "f" * 32
    for index in range(12):
        repo_chat.append_turn(session_id, "问" * 200 + str(index), "答" * 200)

    await chat_service._maybe_summarize(session_id)

    session = repo_chat.load_session(session_id)
    assert session is not None
    assert session["summary"] == "用户在反复确认多模态检索的实现细节。"
    assert session["summary_upto"] == 24 - 6  # 最近 6 条保留原文
    assert len(fake_summary) == 1

    remaining = repo_chat.unsummarized(session_id, session["summary_upto"])
    assert [item["seq"] for item in remaining] == list(range(19, 25))


async def test_summary_skips_when_nothing_to_compress(mysql_clean, fake_summary) -> None:
    session_id = "g" * 32
    repo_chat.append_turn(session_id, "短问题", "短回答")

    await chat_service._maybe_summarize(session_id)

    assert fake_summary == []
    assert repo_chat.load_session(session_id)["summary"] is None
