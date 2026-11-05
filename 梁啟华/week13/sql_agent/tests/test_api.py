"""REST 接口测试（pytest + FastAPI TestClient）。

不依赖真实大模型 Key 和真实数据库：使用临时的 SQLite 数据库，并通过
``create_app(agent_factory=...)`` 注入带假 LLM 的 LangGraphSQLAgent。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api import create_app  # noqa: E402
from langgraph_agent import LangGraphSQLAgent  # noqa: E402

CUSTOMER_TOTAL_SQL = (
    "SELECT c.name, SUM(i.total) AS total "
    "FROM customers c JOIN invoices i ON i.customer_id = c.id "
    "GROUP BY c.name ORDER BY total DESC"
)


class FakeLLM:
    """与 LLMClient 接口对齐的假客户端。"""

    def __init__(
        self,
        sql_responses: list[str],
        summary: str = "这是汇总回答。",
        tables: list[str] | None = None,
    ) -> None:
        self.sql_responses = list(sql_responses)
        self.summary = summary
        self.tables = list(tables or ["customers", "invoices"])

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        prompt = messages[-1]["content"]
        if "SQL 修复专家" in prompt or "SQL 专家" in prompt:
            if not self.sql_responses:
                raise AssertionError("SQL 响应脚本已耗尽")
            return self.sql_responses.pop(0)
        if "数据分析助手" in prompt:
            return self.summary
        raise AssertionError(f"未识别的 chat prompt：{prompt[:80]}")

    def chat_json(self, messages: list[dict]) -> dict:
        prompt = messages[-1]["content"]
        if "资深的数据库分析师" in prompt:
            return {"tables": self.tables, "reason": "测试选择"}
        if "SQL 审核专家" in prompt:
            return {"valid": True, "reason": "ok"}
        raise AssertionError(f"未识别的 chat_json prompt：{prompt[:80]}")


@pytest.fixture()
def demo_db(tmp_path: Path) -> str:
    db_path = tmp_path / "demo.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE customers (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            country TEXT
        );
        CREATE TABLE invoices (
            id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            total REAL NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(id)
        );
        INSERT INTO customers VALUES (1, 'Alice', 'CN');
        INSERT INTO customers VALUES (2, 'Bob', 'US');
        INSERT INTO customers VALUES (3, 'Carol', 'DE');
        INSERT INTO invoices VALUES (1, 1, 10.5);
        INSERT INTO invoices VALUES (2, 1, 20.0);
        INSERT INTO invoices VALUES (3, 2, 30.0);
        INSERT INTO invoices VALUES (4, 3, 5.0);
        """
    )
    conn.commit()
    conn.close()
    return str(db_path)


def _client(demo_db: str, llm: FakeLLM, **agent_kwargs) -> TestClient:
    app = create_app(
        agent_factory=lambda: LangGraphSQLAgent(
            db_path=demo_db, llm=llm, max_rows=50, **agent_kwargs
        )
    )
    return TestClient(app)


# ------------------------------------------------------------------ 健康检查
def test_health_ok(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["table_count"] == 2
        assert body["engine"] == "LangGraphSQLAgent"
        assert body["db_path"].endswith("demo.db")


def test_health_agent_init_failure(demo_db: str) -> None:
    def failing_factory():
        raise FileNotFoundError("数据库文件不存在：not_exists.db")

    app = create_app(agent_factory=failing_factory)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert "数据库文件不存在" in body["error"]

        # 业务接口同样返回 503，而不是 500
        response = client.post("/query", json={"question": "有哪些客户？"})
        assert response.status_code == 503
        assert "Agent 不可用" in response.json()["detail"]


# ---------------------------------------------------------------------- schema
def test_list_tables(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.get("/schema/tables")
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == 2
        names = {table["name"] for table in body["tables"]}
        assert names == {"customers", "invoices"}
        customers = next(t for t in body["tables"] if t["name"] == "customers")
        assert customers["row_count"] == 3
        field_names = {field["name"] for field in customers["fields"]}
        assert {"id", "name", "country"} <= field_names


def test_get_schema_and_filter(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.get("/schema")
        assert response.status_code == 200
        body = response.json()
        assert body["tables"] == ["customers", "invoices"]
        assert "### 表 customers" in body["schema"]
        assert "### 表 invoices" in body["schema"]

        response = client.get("/schema", params={"tables": "customers"})
        assert response.status_code == 200
        body = response.json()
        assert body["tables"] == ["customers"]
        assert "### 表 invoices" not in body["schema"]

        response = client.get("/schema", params={"tables": "not_a_table"})
        assert response.status_code == 404
        assert "表不存在" in response.json()["detail"]


# ------------------------------------------------------------------------ 问答
def test_query_success(demo_db: str) -> None:
    llm = FakeLLM([CUSTOMER_TOTAL_SQL], summary="Alice 的总消费最高。")
    with _client(demo_db, llm) as client:
        response = client.post("/query", json={"question": "每位客户的总消费是多少？"})
        assert response.status_code == 200
        body = response.json()
        assert body["answer"] == "Alice 的总消费最高。"
        assert body["sql"] == CUSTOMER_TOTAL_SQL
        assert body["attempts"] == 1
        assert body["row_count"] == 3
        assert body["columns"] == ["name", "total"]
        assert body["elapsed_ms"] >= 0
        assert [step["stage"] for step in body["trace"]] == [
            "数据库解析",
            "table 选择",
            "SQL 生成",
            "SQL 校验",
            "SQL 执行",
            "结果汇总",
        ]


def test_query_without_rows(demo_db: str) -> None:
    llm = FakeLLM([CUSTOMER_TOTAL_SQL], summary="只返回行数。")
    with _client(demo_db, llm) as client:
        response = client.post(
            "/query",
            json={"question": "每位客户的总消费是多少？", "include_rows": False},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["rows"] == []
        assert body["row_count"] == 3


def test_query_empty_question_returns_422(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.post("/query", json={"question": "   "})
        assert response.status_code == 422
        assert isinstance(response.json()["detail"], list)

        response = client.post("/query", json={})
        assert response.status_code == 422


def test_query_business_failure_returns_422(demo_db: str) -> None:
    llm = FakeLLM(["DELETE FROM invoices", "DELETE FROM invoices"])
    with _client(demo_db, llm, max_rewrite_rounds=1) as client:
        response = client.post("/query", json={"question": "删除所有账单"})
        assert response.status_code == 422
        body = response.json()
        assert body["code"] == "sql_agent_error"
        assert "未通过校验" in body["detail"]


# -------------------------------------------------------------------- SQL 能力
def test_validate_sql_valid_and_invalid(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.post("/sql/validate", json={"sql": "SELECT 1"})
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is True
        assert body["message"] == "ok"

        response = client.post(
            "/sql/validate", json={"sql": "DELETE FROM invoices"}
        )
        assert response.status_code == 200
        body = response.json()
        assert body["valid"] is False
        assert "仅允许执行 SELECT 查询" in body["message"]

        response = client.post(
            "/sql/validate", json={"sql": "SELECT 1; SELECT 2"}
        )
        assert response.status_code == 200
        assert response.json()["valid"] is False


def test_execute_sql_success_and_truncation(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.post(
            "/sql/execute",
            json={"sql": "SELECT id, name FROM customers ORDER BY id", "max_rows": 2},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["columns"] == ["id", "name"]
        assert body["row_count"] == 2
        assert body["truncated"] is True
        assert body["rows"][0] == [1, "Alice"]


def test_execute_sql_rejects_non_readonly(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.post("/sql/execute", json={"sql": "DELETE FROM invoices"})
        assert response.status_code == 400
        assert "SQL 校验失败" in response.json()["detail"]

        response = client.post(
            "/sql/execute", json={"sql": "SELECT * FROM customers", "max_rows": 0}
        )
        assert response.status_code == 422


def test_execute_sql_syntax_error(demo_db: str) -> None:
    with _client(demo_db, FakeLLM([CUSTOMER_TOTAL_SQL])) as client:
        response = client.post("/sql/execute", json={"sql": "SELECT FROM WHERE"})
        assert response.status_code == 400
        assert "SQL 校验失败" in response.json()["detail"]
