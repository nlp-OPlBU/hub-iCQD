"""LangGraph 版 SQL Agent 的核心链路测试。

测试不依赖真实大模型和真实数据库：
- 使用临时 SQLite 数据库（tmp_path）承载 customers / invoices 两张表；
- 使用 ScriptedLLM 假客户端按 Prompt 类型返回预设内容。
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

# 保证从项目根目录直接运行 pytest 时能导入同级模块
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from langgraph_agent import LangGraphSQLAgent  # noqa: E402
from sql_agent import SQLAgent, SQLAgentError  # noqa: E402

CUSTOMER_TOTAL_SQL = (
    "SELECT c.name, SUM(i.total) AS total "
    "FROM customers c JOIN invoices i ON i.customer_id = c.id "
    "GROUP BY c.name ORDER BY total DESC"
)


class ScriptedLLM:
    """按 Prompt 内容返回预设响应的假 LLM，接口与 LLMClient 对齐。"""

    def __init__(
        self,
        sql_responses: list[str],
        tables: list[str] | None = None,
        reviews: list[dict] | None = None,
        summary: str = "这是汇总回答。",
    ) -> None:
        self.sql_responses = list(sql_responses)
        self.tables = list(tables or ["customers", "invoices"])
        self.reviews = list(reviews or [])
        self.summary = summary
        self.chat_prompts: list[str] = []
        self.json_prompts: list[str] = []

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        prompt = messages[-1]["content"]
        self.chat_prompts.append(prompt)
        if "SQL 修复专家" in prompt or "SQL 专家" in prompt:
            if not self.sql_responses:
                raise AssertionError("SQL 响应脚本已耗尽")
            return self.sql_responses.pop(0)
        if "数据分析助手" in prompt:
            return self.summary
        raise AssertionError(f"未识别的 chat prompt：{prompt[:80]}")

    def chat_json(self, messages: list[dict]) -> dict:
        prompt = messages[-1]["content"]
        self.json_prompts.append(prompt)
        if "资深的数据库分析师" in prompt:
            return {"tables": self.tables, "reason": "测试选择"}
        if "SQL 审核专家" in prompt:
            if self.reviews:
                return self.reviews.pop(0)
            return {"valid": True, "reason": "ok"}
        raise AssertionError(f"未识别的 chat_json prompt：{prompt[:80]}")


@pytest.fixture()
def demo_db(tmp_path: Path) -> str:
    """创建包含外键关系的小型 SQLite 数据库。"""
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


def _make_agent(demo_db: str, llm: ScriptedLLM, **kwargs) -> LangGraphSQLAgent:
    return LangGraphSQLAgent(db_path=demo_db, llm=llm, max_rows=50, **kwargs)


def test_happy_path_runs_through_graph(demo_db: str) -> None:
    llm = ScriptedLLM(
        sql_responses=[CUSTOMER_TOTAL_SQL],
        summary="Alice 的总消费最高，为 30.5。",
    )
    agent = _make_agent(demo_db, llm)
    try:
        result = agent.ask("每位客户的总消费是多少？")

        assert result.attempts == 1
        assert result.tables == ["customers", "invoices"]
        assert result.sql == CUSTOMER_TOTAL_SQL
        assert result.columns == ["name", "total"]
        assert result.row_count == 3
        assert result.truncated is False
        assert result.answer == "Alice 的总消费最高，为 30.5。"
        assert [step.stage for step in result.trace] == [
            "数据库解析",
            "table 选择",
            "SQL 生成",
            "SQL 校验",
            "SQL 执行",
            "结果汇总",
        ]
    finally:
        agent.close()


def test_invalid_sql_is_rewritten_once(demo_db: str) -> None:
    llm = ScriptedLLM(
        sql_responses=["DELETE FROM invoices", CUSTOMER_TOTAL_SQL],
        summary="改写后查询成功。",
    )
    agent = _make_agent(demo_db, llm, max_rewrite_rounds=2)
    try:
        result = agent.ask("每位客户的总消费是多少？")

        assert result.attempts == 2
        assert result.sql == CUSTOMER_TOTAL_SQL
        assert result.answer == "改写后查询成功。"
        stages = [step.stage for step in result.trace]
        assert "SQL 改写" in stages
        # 第一轮校验不通过，第二轮通过
        validation_steps = [s for s in result.trace if s.stage == "SQL 校验"]
        assert validation_steps[0].detail.startswith("未通过：")
        assert validation_steps[1].detail == "通过"
    finally:
        agent.close()


def test_rewrite_budget_exhausted_raises(demo_db: str) -> None:
    llm = ScriptedLLM(
        sql_responses=[
            "DELETE FROM invoices",
            "DELETE FROM invoices",
        ]
    )
    agent = _make_agent(demo_db, llm, max_rewrite_rounds=1)
    try:
        with pytest.raises(SQLAgentError, match="2 次生成"):
            agent.ask("每位客户的总消费是多少？")
        assert agent._trace[-1].stage == "SQL 校验"
        assert "仅允许执行 SELECT 查询" in agent._trace[-1].detail
    finally:
        agent.close()


def test_semantic_rejection_triggers_rewrite(demo_db: str) -> None:
    llm = ScriptedLLM(
        sql_responses=["SELECT name FROM customers", CUSTOMER_TOTAL_SQL],
        reviews=[{"valid": False, "reason": "缺少金额聚合"}],
        summary="语义校验后改写成功。",
    )
    agent = _make_agent(demo_db, llm, max_rewrite_rounds=2)
    try:
        result = agent.ask("每位客户的总消费是多少？")

        assert result.attempts == 2
        assert result.sql == CUSTOMER_TOTAL_SQL
        failed_check = [s for s in result.trace if s.stage == "SQL 校验"][0]
        assert "语义校验未通过" in failed_check.detail
        assert "缺少金额聚合" in failed_check.detail
    finally:
        agent.close()


def test_execution_failure_routes_to_rewrite(demo_db: str, monkeypatch) -> None:
    llm = ScriptedLLM(
        sql_responses=[CUSTOMER_TOTAL_SQL, CUSTOMER_TOTAL_SQL],
        summary="执行失败后改写成功。",
    )
    agent = _make_agent(demo_db, llm, max_rewrite_rounds=1)
    original_execute = agent.parser.execute_sql
    calls = {"count": 0}

    def flaky_execute(sql: str, max_rows: int = 200):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("database is locked")
        return original_execute(sql, max_rows)

    monkeypatch.setattr(agent.parser, "execute_sql", flaky_execute)
    try:
        result = agent.ask("每位客户的总消费是多少？")

        assert result.attempts == 2
        execution_steps = [s for s in result.trace if s.stage == "SQL 执行"]
        assert len(execution_steps) == 2
        assert "database is locked" in execution_steps[0].detail
    finally:
        agent.close()


def test_empty_result_uses_fixed_answer(demo_db: str) -> None:
    llm = ScriptedLLM(
        sql_responses=["SELECT name FROM customers WHERE country = 'FR'"],
        summary="不应被调用",
    )
    agent = _make_agent(demo_db, llm, semantic_check=False)
    try:
        result = agent.ask("法国的客户有哪些？")

        assert result.row_count == 0
        assert result.answer == "没有查询到符合条件的数据。"
        assert "数据分析助手" not in "".join(llm.chat_prompts)
    finally:
        agent.close()


def test_empty_question_rejected(demo_db: str) -> None:
    agent = _make_agent(demo_db, ScriptedLLM(sql_responses=[CUSTOMER_TOTAL_SQL]))
    try:
        with pytest.raises(ValueError, match="问题不能为空"):
            agent.ask("   ")
    finally:
        agent.close()


def test_classic_agent_still_works(demo_db: str) -> None:
    """确认原有线性 Agent 未被破坏（兼容性回归）。"""
    llm = ScriptedLLM(
        sql_responses=[CUSTOMER_TOTAL_SQL],
        summary="经典实现仍然可用。",
    )
    agent = SQLAgent(db_path=demo_db, llm=llm, semantic_check=False)
    try:
        result = agent.ask("每位客户的总消费是多少？")

        assert result.row_count == 3
        assert result.answer == "经典实现仍然可用。"
        assert result.attempts == 1
    finally:
        agent.close()
