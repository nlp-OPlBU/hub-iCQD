"""HTTP 契约、资源生命周期和真实 LangGraph 并发行为。"""

import asyncio
import logging
import sqlite3
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient

import sql_agent.api.app as app_module
import sql_agent.api.dependencies as dependencies
import sql_agent.workflow as workflow_module
from sql_agent.models import FinalAnswer, SQLDraft, TableSelection, WorkflowOutput


QUERY = "/api/v1/queries"


def assert_error(response, status, code):
    assert response.status_code == status
    payload = response.json()
    assert set(payload) == {"error"}
    assert set(payload["error"]) == {"code", "message", "details"}
    assert payload["error"]["code"] == code
    assert isinstance(payload["error"]["details"], list)
    UUID(response.headers["X-Request-ID"])


@pytest.mark.parametrize("outcome", ["success", "clarification", "empty", "fallback", "truncated"])
def test_query_outputs(client, api_workflow, outcome):
    result = api_workflow.run.return_value
    updates = {
        "success": {},
        "clarification": {"answer": "请明确查询范围。", "sql": None, "row_count": 0, "tables": []},
        "empty": {"answer": "未查询到符合条件的数据。", "row_count": 0},
        "fallback": {"answer": "| Name |\n| --- |\n| A |", "caveats": ["汇总降级为表格输出"]},
        "truncated": {"truncated": True, "row_count": 2, "caveats": ["仅汇总前 1 行"]},
    }[outcome]
    result = result.model_copy(update=updates)
    api_workflow.run.return_value = result
    response = client.post(QUERY, json={"question": "  列出歌曲\n"})
    assert response.status_code == 200
    assert response.json() == result.model_dump()
    api_workflow.run.assert_awaited_once_with("列出歌曲")


@pytest.mark.parametrize("payload", [
    {}, {"question": None}, {"question": 123}, {"question": True},
    {"question": []}, {"question": ""}, {"question": " \t\n"},
    {"question": "字" * 2001}, {"question": "查询", "sql": "SELECT 1"},
    [], "查询",
])
def test_invalid_fields_do_not_run(client, api_workflow, payload):
    response = client.post(QUERY, json=payload)
    assert_error(response, 422, "validation_error")
    api_workflow.run.assert_not_awaited()
    assert "input" not in response.json()["error"]


def test_normalized_length_boundary(client, api_workflow):
    response = client.post(QUERY, json={"question": "  " + "字" * 2000 + "\n"})
    assert response.status_code == 200
    api_workflow.run.assert_awaited_once_with("字" * 2000)


@pytest.mark.parametrize("body", [b'{"question":', b"", b"null", b"\xff"])
def test_invalid_json(client, api_workflow, body):
    response = client.post(QUERY, content=body, headers={"Content-Type": "application/json"})
    assert_error(response, 422, "validation_error")
    api_workflow.run.assert_not_awaited()


@pytest.mark.parametrize("media_type", [None, "text/plain", "application/x-www-form-urlencoded"])
def test_wrong_media_type_checked_before_json(client, api_workflow, media_type):
    headers = {"Content-Type": media_type} if media_type else {}
    response = client.post(QUERY, content=b"not-json", headers=headers)
    assert_error(response, 415, "unsupported_media_type")
    api_workflow.run.assert_not_awaited()


@pytest.mark.parametrize("media_type", ["application/json; charset=utf-8", "application/vnd.query+json"])
def test_json_media_types(client, media_type):
    response = client.post(QUERY, json={"question": "查询"}, headers={"Content-Type": media_type})
    assert response.status_code == 200


@pytest.mark.parametrize("failure", ["workflow", "exception", "wrong_type"])
def test_failures_are_sanitized_and_correlated(client, api_workflow, caplog, failure):
    private_detail = "internal-endpoint/private/database-path"
    if failure == "workflow":
        api_workflow.run.return_value = WorkflowOutput(
            question="查询", answer=private_detail, error=private_detail,
        )
    elif failure == "exception":
        api_workflow.run.side_effect = RuntimeError(private_detail)
    else:
        api_workflow.run.return_value = {"answer": private_detail}
    with caplog.at_level(logging.INFO):
        response = client.post(QUERY, json={"question": "查询"})
    assert_error(response, 500, "workflow_failed" if failure == "workflow" else "internal_error")
    assert private_detail not in response.text
    assert response.headers["X-Request-ID"] in caplog.text
    if failure != "wrong_type":
        assert private_detail in caplog.text


def test_liveness_has_no_dependencies(client, api_app, api_workflow, monkeypatch):
    probe = Mock(side_effect=AssertionError("存活检查不应访问数据库"))
    monkeypatch.setattr(dependencies, "check_database", probe)
    api_app.state.workflow = None
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    probe.assert_not_called()
    api_workflow.run.assert_not_awaited()


def test_readiness_uses_lightweight_readonly_connection(client, api_workflow, monkeypatch):
    calls = []
    statements = []
    connection_threads = []
    close_threads = []
    original = sqlite3.connect

    class TrackedConnection(sqlite3.Connection):
        def close(self):
            super().close()
            # 在创建连接的工作线程内验证关闭，不跨线程访问 SQLite 对象。
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                self.execute("SELECT 1")
            close_threads.append(threading.get_ident())

    def connect(*args, **kwargs):
        calls.append((args, kwargs))
        connection = original(*args, **kwargs, factory=TrackedConnection)
        connection.set_trace_callback(statements.append)
        connection_threads.append(threading.get_ident())
        return connection

    monkeypatch.setattr(dependencies.sqlite3, "connect", connect)
    response = client.get("/readyz")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}
    assert len(calls) == 1
    assert calls[0][0][0].endswith("?mode=ro")
    assert calls[0][1]["uri"] is True
    assert statements == ["PRAGMA query_only = ON", "SELECT 1"]
    assert close_threads == connection_threads
    api_workflow.run.assert_not_awaited()


@pytest.mark.parametrize("unavailable", ["workflow", "database"])
def test_unavailable_service(client, api_app, api_workflow, unavailable, tmp_path):
    if unavailable == "workflow":
        api_app.state.workflow = None
    else:
        api_app.state.db_path = tmp_path / "missing.db"
    for response in [client.get("/readyz"), client.post(QUERY, json={"question": "查询"})]:
        assert_error(response, 503, "service_unavailable")
    assert not (tmp_path / "missing.db").exists()
    api_workflow.run.assert_not_awaited()


def test_request_ids_and_framework_errors(client):
    first = client.get("/healthz", headers={"X-Request-ID": "client-supplied"})
    second = client.get("/healthz")
    assert first.headers["X-Request-ID"] != "client-supplied"
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
    assert_error(client.get("/missing"), 404, "not_found")
    response = client.get(QUERY)
    assert_error(response, 405, "method_not_allowed")
    assert "POST" in response.headers["Allow"]


def test_docs_and_openapi_contract(client):
    assert client.get("/docs").status_code == 200
    schema = client.get("/openapi.json").json()
    responses = schema["paths"][QUERY]["post"]["responses"]
    assert responses["200"]["content"]["application/json"]["schema"]["$ref"].endswith("WorkflowOutput")
    for status in (415, 422, 500, 503):
        assert responses[str(status)]["content"]["application/json"]["schema"]["$ref"].endswith("ErrorResponse")
    request = schema["components"]["schemas"]["QueryRequest"]
    assert request["additionalProperties"] is False
    assert request["properties"]["question"]["maxLength"] == 2000


def test_workflow_dependency_can_be_overridden(client, api_app, api_workflow):
    replacement = SimpleNamespace(run=AsyncMock(return_value=WorkflowOutput(question="查询", answer="替换结果")))
    api_app.dependency_overrides[dependencies.get_workflow] = lambda: replacement
    response = client.post(QUERY, json={"question": "查询"})
    assert response.json()["answer"] == "替换结果"
    api_workflow.run.assert_not_awaited()


def test_lifecycle_releases_owned_engines(api_app, tracked_engines):
    engines, disposed = tracked_engines
    with TestClient(api_app) as client:
        assert client.get("/readyz").status_code == 200
        assert len(engines) == 2
        assert disposed == []
    assert set(disposed) == set(engines)
    assert api_app.state.workflow is None
    assert api_app.state.db_path is None


@pytest.mark.parametrize("failure_point", ["catalog", "workflow"])
def test_failed_startup_releases_partial_resources(api_config, tracked_engines, monkeypatch, failure_point):
    target = "SchemaCatalog" if failure_point == "catalog" else "SQLAgentWorkflow"
    monkeypatch.setattr(app_module, target, Mock(side_effect=RuntimeError("启动失败")))
    app = app_module.create_app()
    with pytest.raises(RuntimeError, match="启动失败"):
        with TestClient(app):
            pytest.fail("启动异常必须向外传播")
    engines, disposed = tracked_engines
    assert len(engines) == (1 if failure_point == "catalog" else 2)
    assert set(disposed) == set(engines)
    assert app.state.workflow is None


def test_real_graph_concurrent_requests_remain_isolated(api_config, monkeypatch):
    async def scenario():
        both_started = asyncio.Event()
        questions = []

        async def select_tables(question, feedback=""):
            questions.append(question)
            if len(questions) == 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), 3)
            return TableSelection(tables=["tracks"], reason="测试")

        async def generate_sql(question, schema):
            return SQLDraft(sql="SELECT missing FROM tracks" if question == "first" else "SELECT Name FROM tracks")

        monkeypatch.setattr(workflow_module, "sql_rewriter_agent", SimpleNamespace(
            ainvoke=AsyncMock(return_value={"structured_response": SQLDraft(sql="SELECT Name FROM tracks WHERE TrackId = 1")}),
        ))
        monkeypatch.setattr(workflow_module, "result_summary_agent", SimpleNamespace(
            ainvoke=AsyncMock(side_effect=RuntimeError("测试表格降级")),
        ))
        app = app_module.create_app()
        async with app.router.lifespan_context(app):
            workflow = app.state.workflow
            monkeypatch.setattr(workflow, "_select_tables", select_tables)
            monkeypatch.setattr(workflow, "_generate_sql", generate_sql)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                responses = await asyncio.gather(*[
                    client.post(QUERY, json={"question": question}) for question in ("first", "second")
                ])
        first, second = [response.json() for response in responses]
        assert all(response.status_code == 200 for response in responses)
        assert [first["question"], second["question"]] == ["first", "second"]
        assert [first["repair_attempts"], second["repair_attempts"]] == [1, 0]
        assert [first["row_count"], second["row_count"]] == [1, 2]
        assert "| B |" not in first["answer"] and "| B |" in second["answer"]
        assert first["error"] is second["error"] is None

    asyncio.run(scenario())


def test_health_responds_while_real_graph_executes_sql(api_config, monkeypatch):
    agents = {
        "table_selector_agent": TableSelection(tables=["tracks"], reason="测试"),
        "sql_generator_agent": SQLDraft(sql="SELECT Name FROM tracks"),
        "result_summary_agent": FinalAnswer(answer="A、B"),
    }
    for name, result in agents.items():
        monkeypatch.setattr(workflow_module, name, SimpleNamespace(
            ainvoke=AsyncMock(return_value={"structured_response": result}),
        ))

    async def scenario():
        app = app_module.create_app()
        started, release = threading.Event(), threading.Event()
        async with app.router.lifespan_context(app):
            execute = app.state.workflow.executor.execute

            def slow_execute(sql):
                started.set()
                if not release.wait(5):
                    raise RuntimeError("健康检查未能在 SQL 执行期间响应")
                return execute(sql)

            monkeypatch.setattr(app.state.workflow.executor, "execute", slow_execute)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                query_task = asyncio.create_task(client.post(QUERY, json={"question": "列出歌曲"}))
                try:
                    assert await asyncio.to_thread(started.wait, 3)
                    response = await asyncio.wait_for(client.get("/healthz"), 2)
                    assert response.status_code == 200
                    assert not query_task.done()
                finally:
                    release.set()
                    result = await query_task
                assert result.status_code == 200
                assert result.json()["row_count"] == 2

    asyncio.run(scenario())
