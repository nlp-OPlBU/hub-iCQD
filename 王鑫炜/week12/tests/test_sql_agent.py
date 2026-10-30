import hashlib
import json
from pathlib import Path

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from sql_agent.agent import LangChainSQLAgent
from sql_agent.database import Database, QueryError
from sql_agent.demo import ScriptedChatModel, call, make_demo

DB = Path(__file__).resolve().parents[1] / "sql_agent" / "data" / "chinook.db"


@pytest.mark.parametrize("name", ["count", "join", "rewrite"])
def test_langchain_tool_graph_and_real_sqlite(name):
    question, model = make_demo(name)
    result = LangChainSQLAgent(Database(DB), model, mode="offline_replay").run(question)
    assert result["status"] == "ok", result
    assert result["mode"] == "offline_replay"
    assert result["queries"]
    if name == "count":
        assert result["queries"][0]["result"]["rows"] == [[204]]
    if name == "rewrite":
        assert result["tool_errors"] == 1
        assert result["queries"][0]["result"]["rows"] == [[2328.6]]
        assert any(t.get("ok") is False and "Amount" in t.get("error", "") for t in result["trace"])
    if name == "join":
        assert "tracks" in result["selected_tables"]
        assert len(result["queries"][0]["result"]["rows"]) == 5


@pytest.mark.parametrize("sql", [
    "DELETE FROM albums", "UPDATE albums SET Title='changed'", "DROP TABLE albums",
    "ATTACH DATABASE ':memory:' AS other", "PRAGMA query_only=OFF",
    "SELECT load_extension('bad')", "SELECT readfile('/etc/passwd')",
    "SELECT Email FROM customers", "SELECT name FROM sqlite_master",
    "SELECT COUNT(*) FROM albums; DELETE FROM albums",
])
def test_database_rejects_dangerous_operations_without_changes(sql):
    before = hashlib.sha256(DB.read_bytes()).hexdigest()
    database = Database(DB)
    with pytest.raises(QueryError):
        database.validate(sql, ["albums", "customers"])
        database.execute(sql, ["albums", "customers"])
    assert hashlib.sha256(DB.read_bytes()).hexdigest() == before


def test_policy_error_stops_followup_queries():
    responses = [call("describe_tables", {"table_names": ["albums"]}, 1),
                 call("check_sql", {"sql": "DELETE FROM albums"}, 2),
                 call("check_sql", {"sql": "SELECT COUNT(*) FROM albums"}, 3),
                 call("query_sql", {"sql": "SELECT COUNT(*) FROM albums"}, 4),
                 AIMessage(content="不能继续")]
    result = LangChainSQLAgent(Database(DB), ScriptedChatModel(responses=responses)).run("查询专辑")
    assert result["status"] == "blocked"
    assert result["queries"] == []


def test_query_cannot_skip_validation():
    responses = [call("describe_tables", {"table_names": ["albums"]}, 1),
                 call("query_sql", {"sql": "SELECT COUNT(*) FROM albums"}, 2), AIMessage(content="未执行")]
    result = LangChainSQLAgent(Database(DB), ScriptedChatModel(responses=responses)).run("统计专辑")
    assert result["status"] == "no_query"
    assert not result["queries"] and result["tool_errors"] == 1


def test_result_truncation_and_empty_results():
    database = Database(DB, max_rows=2)
    result = database.execute("SELECT ArtistId FROM artists ORDER BY ArtistId", ["artists"])
    assert result["truncated"] and len(result["rows"]) == 2
    assert database.execute("SELECT ArtistId FROM artists WHERE ArtistId < 0", ["artists"])["rows"] == []


def test_instruction_budget_and_execution_error():
    database = Database(DB, max_steps=1000)
    with pytest.raises(QueryError, match="interrupted"):
        database.execute("WITH RECURSIVE n(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM n WHERE x<10000000) SELECT SUM(x) FROM n", [])
    # 编译成功不代表执行成功，执行层仍必须捕获错误。
    database.validate("SELECT ABS(-9223372036854775808)", [])
    with pytest.raises(QueryError, match="overflow"):
        database.execute("SELECT ABS(-9223372036854775808)", [])


def test_real_chatopenai_serialization_and_tool_feedback_with_mock_transport():
    question, scripted = make_demo("rewrite")
    replies = scripted.responses.copy()
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.path == "/v1/chat/completions"
        reply = replies.pop(0)
        message = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            message["tool_calls"] = [{"id": c["id"], "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["args"])}} for c in reply.tool_calls]
        return httpx.Response(200, json={"id": "sql-test", "object": "chat.completion", "created": 0,
            "model": "qwen-plus", "choices": [{"index": 0, "message": message,
                "finish_reason": "tool_calls" if reply.tool_calls else "stop"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        model = ChatOpenAI(model="qwen-plus", api_key="test-only", base_url="http://localhost/v1",
                           use_responses_api=False, http_client=client, max_retries=0)
        result = LangChainSQLAgent(Database(DB), model).run(question)
    assert result["status"] == "ok" and result["tool_errors"] == 1
    assert not replies
    assert "tools" in requests[0]
    assert any(m["role"] == "tool" and "no such column" in str(m["content"])
               for m in requests[-1]["messages"])
