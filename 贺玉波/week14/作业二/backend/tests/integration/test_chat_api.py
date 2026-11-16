"""问答接口集成测试：真实 Redis，检索与 vLLM 在边界处替换。

替换的是"模型/网络边界"，中间件（Redis）保持真实：
验证会话写入、SSE 事件顺序、无命中时不调用模型。
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

from app.graph import qa_graph
from app.services import chat_service

pytestmark = pytest.mark.integration


def _hits() -> list[dict]:
    return [
        {
            "chunk_id": "d-0000",
            "doc_id": "d" * 32,
            "kind": "text",
            "page_no": 3,
            "text": "多模态 RAG 同时检索文本与图片。",
            "score": 0.031,
            "sources": ["text"],
            "image_path": None,
        }
    ]


@pytest.fixture()
async def client(redis_clean, mysql_clean):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture()
def fake_retrieval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(qa_graph.retrieval_service, "search", lambda *args, **kwargs: (_hits(), 7))


@pytest.fixture()
def fake_llm(monkeypatch: pytest.MonkeyPatch) -> list[list[dict]]:
    calls: list[list[dict]] = []

    async def completion(messages, **kwargs):
        calls.append(messages)
        return "多模态 RAG 会同时检索文本和图片 [1]。"

    async def stream(messages, **kwargs):
        for piece in ("多模态 ", "RAG 会同时", "检索文本和图片 [1]。"):
            yield piece

    monkeypatch.setattr(chat_service.vllm_client, "chat_completion", completion)
    monkeypatch.setattr(chat_service.vllm_client, "stream_chat", stream)
    return calls


async def test_chat_returns_answer_and_citations(client: AsyncClient, fake_retrieval, fake_llm) -> None:
    response = await client.post("/api/v1/chat", json={"question": "什么是多模态 RAG？"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"].startswith("多模态 RAG")
    assert body["citations"][0]["page_no"] == 3
    assert body["need_retrieval"] is True
    assert fake_llm and "【资料】" in fake_llm[0][-1]["content"]


async def test_chat_history_persisted_in_mysql(client: AsyncClient, fake_retrieval, fake_llm) -> None:
    response = await client.post("/api/v1/chat", json={"question": "多模态检索怎么做的"})
    session_id = response.json()["session_id"]

    history = await client.get(f"/api/v1/chat/{session_id}/history")
    roles = [item["role"] for item in history.json()["messages"]]
    assert roles == ["user", "assistant"]


async def test_chat_history_survives_cache_loss(
    client: AsyncClient, fake_retrieval, fake_llm, redis_clean
) -> None:
    """Redis 是缓存不是真源：清掉缓存后历史仍应从 MySQL 读出来。"""
    created = await client.post("/api/v1/chat", json={"question": "多模态检索怎么做的"})
    session_id = created.json()["session_id"]
    redis_clean.flushdb()

    history = await client.get(f"/api/v1/chat/{session_id}/history")
    messages = history.json()["messages"]
    assert [item["role"] for item in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "多模态检索怎么做的"


async def test_chat_without_hits_still_answers(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch, fake_llm
) -> None:
    """检索为空不等于拒答：仍要调模型，并明确告知内容不来自知识库。"""
    monkeypatch.setattr(qa_graph.retrieval_service, "search", lambda *args, **kwargs: ([], 3))
    response = await client.post("/api/v1/chat", json={"question": "库里没有的主题"})
    assert response.status_code == 200
    assert response.json()["answer"]
    assert response.json()["citations"] == []
    assert fake_llm, "无命中时也应由模型兜底回答"
    systems = [item["content"] for item in fake_llm[0] if item["role"] == "system"]
    assert any("不来自知识库" in text for text in systems)


async def test_chat_stream_events(client: AsyncClient, fake_retrieval, fake_llm) -> None:
    async with client.stream(
        "POST",
        "/api/v1/chat",
        json={"question": "多模态检索", "stream": True},
    ) as response:
        assert response.status_code == 200
        body = "".join([chunk async for chunk in response.aiter_text()])

    assert "event: meta" in body
    assert "event: delta" in body
    assert "event: done" in body
    assert body.index("event: meta") < body.index("event: done")


async def test_reset_session(client: AsyncClient, fake_retrieval, fake_llm) -> None:
    created = await client.post("/api/v1/chat", json={"question": "多模态检索"})
    session_id = created.json()["session_id"]
    assert (await client.delete(f"/api/v1/chat/{session_id}")).status_code == 204
    history = await client.get(f"/api/v1/chat/{session_id}/history")
    assert history.json()["messages"] == []


async def test_stream_disconnect_persists_partial_answer(
    redis_clean, mysql_clean, fake_retrieval, monkeypatch: pytest.MonkeyPatch
) -> None:
    """客户端中途断流时，已经吐出的部分回答也要落库，避免会话只剩问题。"""
    persisted: list[tuple[str, str]] = []

    async def record(session_id: str, question: str, answer: str, citations=None) -> None:
        persisted.append((question, answer))

    async def stream(messages, **kwargs):
        yield "第一段"
        yield "第二段"
        yield "第三段"

    monkeypatch.setattr(chat_service, "_persist", record)
    monkeypatch.setattr(chat_service.vllm_client, "stream_chat", stream)

    session_id = "s" * 32
    agen = chat_service.ask_stream("多模态检索", session_id=session_id)
    seen: list[str] = []
    async for name, data in agen:
        if name == "delta":
            seen.append(data)
            if len(seen) == 2:
                await agen.aclose()  # 模拟浏览器关闭连接
                break

    await asyncio.sleep(0)  # 让 finally 里 create_task 排的后台落库跑起来
    assert seen == ["第一段", "第二段"]
    assert persisted == [("多模态检索", "第一段第二段")]
