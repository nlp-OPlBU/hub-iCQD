"""HTTP API 测试（mock LLM，无需 API key）。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from sql_agent.api.app import app
from sql_agent.llm import StructuredAgent
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection


def _patch_agents(monkeypatch, outputs):
    calls = []

    async def fake_ainvoke(self, text):
        calls.append((self.name, text))
        return outputs[len(calls) - 1]

    monkeypatch.setattr(StructuredAgent, "ainvoke", fake_ainvoke)
    return calls


def test_health() -> None:
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["backend"] == "langgraph"
    assert "albums" in data["tables"]


def test_query(monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    with TestClient(app) as client:
        r = client.post("/api/query", json={"question": "总共有多少张专辑？"})

    assert r.status_code == 200
    data = r.json()
    assert data["answer"] == "共 347 张专辑。"
    assert data["tables"] == ["albums"]
    assert data["error"] is None


def test_query_stream(monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    with TestClient(app) as client:
        with client.stream(
            "POST", "/api/query/stream", json={"question": "总共有多少张专辑？"}
        ) as r:
            text = "".join(r.iter_text())

    assert r.status_code == 200
    assert "event: stage" in text
    assert "event: result" in text
    assert "共 347 张专辑" in text


def test_query_returns_error_field(monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="bad sql", confidence=0.9),
        SQLDraft(sql="SELECT nope FROM albums", explanation="bad"),
        SQLDraft(sql="", explanation="无法修复", cannot_fix=True),
    ]
    _patch_agents(monkeypatch, outputs)

    with TestClient(app) as client:
        r = client.post("/api/query", json={"question": "测试失败路径"})

    assert r.status_code == 200
    data = r.json()
    assert data["error"] is not None
    assert "当前数据库无法回答该问题" in data["answer"]
