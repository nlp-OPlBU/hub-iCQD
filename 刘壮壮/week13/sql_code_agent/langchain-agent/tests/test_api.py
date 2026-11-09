import json

from fastapi.testclient import TestClient

from langchain_sql_agent.api import create_app
from langchain_sql_agent.graph import QueryGenerationFailed
from langchain_sql_agent.models import (
    AgentRunResult,
    AnswerDelta,
    ProgressUpdate,
    QueryResult,
)


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        event = next(line[7:] for line in lines if line.startswith("event: "))
        data = next(line[6:] for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


class StubAgent:
    last_call = None

    @staticmethod
    def new_request_id() -> str:
        return "trace_api"

    @staticmethod
    def new_conversation_id() -> str:
        return "conv_0123456789abcdef0123456789abcdef"

    async def answer_detailed(
        self,
        question,
        *,
        request_id,
        conversation_id,
        history,
        on_event,
    ):
        self.last_call = (question, conversation_id, history)
        on_event(
            ProgressUpdate(
                stage="select_tables",
                status="running",
                message="正在选择相关数据表",
            )
        )
        on_event(AnswerDelta(delta="共有 "))
        on_event(AnswerDelta(delta="59 位客户。"))
        return AgentRunResult(
            request_id=request_id,
            conversation_id=conversation_id,
            answer="共有 59 位客户。",
            selected_tables=["customers"],
            sql="SELECT COUNT(*) FROM customers",
            query_result=QueryResult(
                columns=["count"],
                rows=[[59]],
                row_count=1,
                truncated=False,
                elapsed_ms=0.1,
            ),
        )


class FailingAgent(StubAgent):
    async def answer_detailed(self, question, **kwargs):
        raise QueryGenerationFailed()


def test_health_check() -> None:
    with TestClient(create_app(lambda: StubAgent())) as client:
        response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_streams_progress_deltas_and_matching_result() -> None:
    with TestClient(create_app(lambda: StubAgent())) as client:
        response = client.post(
            "/api/chat/stream", json={"question": "有多少客户？"}
        )

    assert response.status_code == 200
    events = parse_sse(response.text)
    assert [event for event, _ in events] == [
        "meta",
        "progress",
        "answer_delta",
        "answer_delta",
        "result",
    ]
    streamed_answer = "".join(
        str(data["delta"])
        for event, data in events
        if event == "answer_delta"
    )
    result = next(data for event, data in events if event == "result")
    assert streamed_answer == result["answer"]
    assert result["query_result"]["rows"] == [[59]]
    assert result["conversation_id"] == "conv_0123456789abcdef0123456789abcdef"


def test_forwards_limited_history_and_conversation_id() -> None:
    agent = StubAgent()
    conversation_id = "conv_abcdef0123456789abcdef0123456789"
    with TestClient(create_app(lambda: agent)) as client:
        response = client.post(
            "/api/chat/stream",
            json={
                "question": "那第二名呢？",
                "conversation_id": conversation_id,
                "history": [
                    {
                        "question": "销售额最高的国家？",
                        "answer": "美国。",
                        "sql": "SELECT 1",
                    }
                ],
            },
        )

    assert response.status_code == 200
    assert agent.last_call is not None
    assert agent.last_call[0:2] == ("那第二名呢？", conversation_id)
    assert agent.last_call[2][0].answer == "美国。"


def test_maps_workflow_error_to_public_sse_error() -> None:
    with TestClient(create_app(lambda: FailingAgent())) as client:
        response = client.post(
            "/api/chat/stream", json={"question": "有多少客户？"}
        )

    events = parse_sse(response.text)
    assert events[-1] == (
        "error",
        {
            "request_id": "trace_api",
            "code": "QueryGenerationFailed",
            "message": "无法形成有效查询。",
        },
    )


def test_rejects_empty_question_before_streaming() -> None:
    with TestClient(create_app(lambda: StubAgent())) as client:
        response = client.post("/api/chat/stream", json={"question": ""})

    assert response.status_code == 422


def test_rejects_more_than_five_history_turns() -> None:
    turn = {"question": "问题", "answer": "回答", "sql": None}
    with TestClient(create_app(lambda: StubAgent())) as client:
        response = client.post(
            "/api/chat/stream",
            json={"question": "追问", "history": [turn] * 6},
        )

    assert response.status_code == 422
