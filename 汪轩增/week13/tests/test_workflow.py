import asyncio
from pathlib import Path

import pytest

from sql_agent.config import settings
from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutor
from sql_agent.db.validator import SQLValidator
from sql_agent.llm import StructuredAgent
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection
from sql_agent.workflow import SQLAgentWorkflow


@pytest.fixture(scope="module")
def workflow() -> SQLAgentWorkflow:
    catalog = SchemaCatalog(Path("ref/chinook.db"))
    return SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
    )


def _patch_agents(monkeypatch, outputs):
    """替换 LangChain 链的调用入口，按顺序返回预置的结构化结果。"""

    calls = []

    async def fake_ainvoke(self, text):
        calls.append((self.name, text))
        return outputs[len(calls) - 1]

    monkeypatch.setattr(StructuredAgent, "ainvoke", fake_ainvoke)
    return calls


def test_happy_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]
    assert result.row_count == 1
    assert result.answer == "共 347 张专辑。"
    assert len(calls) == 3


def test_rewrite_path(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="统计专辑数量", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(ArtistId FROM albums", explanation="bad"),
        SQLDraft(sql="SELECT COUNT(ArtistId) AS n FROM albums", explanation="fixed"),
        FinalAnswer(answer="共有 275 位艺人。", evidence=["275"]),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("专辑里有多少位不同的艺人？"))

    assert result.repair_attempts == 1
    assert result.error is None
    assert "COUNT(ArtistId)" in (result.sql or "")


def test_empty_result_skips_summary_agent(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["albums"], reason="查不存在的专辑", confidence=0.9),
        SQLDraft(sql="SELECT * FROM albums WHERE AlbumId = -1", explanation="empty"),
    ]
    calls = _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("编号为 -1 的专辑是什么？"))

    assert result.row_count == 0
    assert result.answer == "未查询到符合条件的数据。"
    assert len(calls) == 2


def test_unknown_table_is_rejected(workflow: SQLAgentWorkflow, monkeypatch) -> None:
    outputs = [
        TableSelection(tables=["not_a_table"], reason="bad", confidence=0.1),
        TableSelection(tables=["albums"], reason="fixed", confidence=0.9),
        SQLDraft(sql="SELECT COUNT(*) AS n FROM albums", explanation="count"),
        FinalAnswer(answer="共 347 张专辑。", evidence=["347"]),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("总共有多少张专辑？"))

    assert result.error is None
    assert result.tables == ["albums"]


def test_max_repair_attempts_returns_error(monkeypatch) -> None:
    catalog = SchemaCatalog(Path("ref/chinook.db"))
    workflow = SQLAgentWorkflow(
        catalog=catalog,
        validator=SQLValidator(catalog.db_path),
        executor=SQLExecutor(catalog.db_path),
        max_repair_attempts=0,
    )
    outputs = [
        TableSelection(tables=["albums"], reason="bad sql", confidence=0.9),
        SQLDraft(sql="SELECT nope FROM albums", explanation="bad"),
    ]
    _patch_agents(monkeypatch, outputs)

    result = asyncio.run(workflow.run("测试失败路径"))

    assert result.error is not None
    assert result.repair_attempts == 0
    assert "最大修复次数" in result.answer


def test_settings_db_path_exists() -> None:
    assert settings.db_path.exists()
