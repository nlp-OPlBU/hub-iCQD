"""短问题改写的触发条件、清洗与失败降级。"""

from __future__ import annotations

import asyncio

import pytest

from app.core.config import get_settings
from app.services import chat_service


def _with_rewrite(enabled: bool):
    base = get_settings()
    return base.model_copy(
        update={"retrieval": base.retrieval.model_copy(update={"rewrite_enabled": enabled})}
    )


@pytest.fixture()
def rewrite_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_service, "get_settings", lambda: _with_rewrite(True))


def _stub(monkeypatch: pytest.MonkeyPatch, reply: str, calls: list | None = None) -> None:
    async def completion(messages, **kwargs):
        if calls is not None:
            calls.append(messages)
        return reply

    monkeypatch.setattr(chat_service.vllm_client, "chat_completion", completion)


def test_short_question_is_rewritten_and_cleaned(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    _stub(monkeypatch, "「RAG 检索增强生成 原理」")
    assert asyncio.run(chat_service._rewrite_query("RAG是什么")) == "RAG 检索增强生成 原理"


def test_long_question_skips_rewrite(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    calls: list = []
    _stub(monkeypatch, "不该被调用", calls)
    assert asyncio.run(chat_service._rewrite_query("这份文档里多模态检索的完整召回策略是怎样设计的")) is None
    assert calls == []


def test_greeting_skips_rewrite(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    calls: list = []
    _stub(monkeypatch, "不该被调用", calls)
    assert asyncio.run(chat_service._rewrite_query("你好")) is None
    assert calls == []


def test_rewrite_failure_falls_back_to_original(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    async def completion(messages, **kwargs):
        raise RuntimeError("模型不可用")

    monkeypatch.setattr(chat_service.vllm_client, "chat_completion", completion)
    assert asyncio.run(chat_service._rewrite_query("RAG是什么")) is None


def test_unchanged_rewrite_is_ignored(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    _stub(monkeypatch, "RAG是什么")
    assert asyncio.run(chat_service._rewrite_query("RAG是什么")) is None


def test_history_is_passed_into_rewrite_prompt(monkeypatch: pytest.MonkeyPatch, rewrite_on) -> None:
    """多轮里的「它的缺点呢」不带历史就补不出指代，检索会跑偏。"""
    calls: list = []
    _stub(monkeypatch, "RAG 的缺点", calls)
    history = [
        {"role": "user", "content": "介绍一下RAG"},
        {"role": "assistant", "content": "RAG 是检索增强生成……"},
    ]
    assert asyncio.run(chat_service._rewrite_query("它的缺点呢", history)) == "RAG 的缺点"
    payload = calls[0][-1]["content"]
    assert "介绍一下RAG" in payload and "它的缺点呢" in payload
