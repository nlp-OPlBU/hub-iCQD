"""SQL Agent 图工作流的单元测试。

所有模型调用均由 FakeGateway 替代，测试只验证确定性的编排和异常处理逻辑。
"""

from pathlib import Path

import pytest

from langchain_sql_agent.config import Settings
from langchain_sql_agent.graph import (
    InvalidTableSelection,
    QueryGenerationFailed,
    QueryTimedOut,
    SqlAgentGraph,
    UnsafeExecutionBlocked,
    WorkflowError,
)
from langchain_sql_agent.models import (
    FinalAnswer,
    QueryResult,
    SqlDraft,
    TableSelection,
)
from langchain_sql_agent.query_executor import (
    QuerySafetyError,
    QueryTimeoutError,
    RepairableQueryError,
)


DB_PATH = Path(__file__).resolve().parents[2] / "data" / "chinook.db"


class FakeGateway:
    """按预设顺序返回结构化结果，并记录工作流调用轨迹。"""

    def __init__(
        self,
        generated_sql: str = "SELECT COUNT(*) FROM customers",
        *,
        selections: list[list[str]] | None = None,
        rewrites: list[str] | None = None,
    ) -> None:
        self.generated_sql = generated_sql
        self.selections = iter(selections or [["customers"]])
        self.rewrites = iter(rewrites or [])
        self.calls: list[str] = []
        self.selection_contexts: list[str] = []
        self.rewrite_histories: list[list[str]] = []

    async def select_tables(
        self, question: str, schema_context: str, request_id: str
    ) -> TableSelection:
        self.calls.append("select")
        self.selection_contexts.append(schema_context)
        return TableSelection(tables=next(self.selections))

    async def generate_sql(
        self, question: str, focused_schema: str, request_id: str
    ) -> SqlDraft:
        self.calls.append("generate")
        return SqlDraft(sql=self.generated_sql)

    async def rewrite_sql(
        self,
        question: str,
        focused_schema: str,
        sql: str,
        error: str,
        history: list[str],
        request_id: str,
    ) -> SqlDraft:
        self.calls.append("rewrite")
        self.rewrite_histories.append(history)
        return SqlDraft(sql=next(self.rewrites))

    async def synthesize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        request_id: str,
    ) -> FinalAnswer:
        self.calls.append("synthesize")
        return FinalAnswer(answer=f"共有 {result.rows[0][0]} 位客户。")


class FailingExecutor:
    """模拟执行器始终抛出指定异常。"""

    def __init__(self, error: Exception) -> None:
        self.error = error
        self.calls = 0

    def execute(self, sql: str) -> QueryResult:
        self.calls += 1
        raise self.error


class RepairOnceExecutor:
    """第一次执行失败、第二次成功，用于验证执行错误的改写路径。"""

    def __init__(self) -> None:
        self.sql: list[str] = []

    def execute(self, sql: str) -> QueryResult:
        self.sql.append(sql)
        if len(self.sql) == 1:
            raise RepairableQueryError("temporary execution failure")
        return QueryResult(
            columns=["COUNT(*)"],
            rows=[[59]],
            row_count=1,
            truncated=False,
            elapsed_ms=0.1,
        )


def make_settings(**overrides: object) -> Settings:
    """创建不访问真实模型服务的测试配置。"""

    values: dict[str, object] = {
        "provider": "deepseek",
        "model": "test-model",
        "api_key": "test-key",
        "db_path": DB_PATH,
        "max_rewrites": 2,
    }
    values.update(overrides)
    return Settings(**values)


async def test_runs_readonly_workflow_end_to_end() -> None:
    # 覆盖选表、生成 SQL、执行和汇总的标准成功路径。
    gateway = FakeGateway()

    answer = await SqlAgentGraph(make_settings(), gateway=gateway).answer(
        " 店里一共有多少位客户？ "
    )

    assert answer.answer == "共有 59 位客户。"
    assert gateway.calls == ["select", "generate", "synthesize"]


async def test_retries_invalid_table_selection_with_feedback() -> None:
    # 首次返回不存在的表，第二次应收到纠错提示并成功去重。
    gateway = FakeGateway(selections=[["missing"], ["customers", "customers"]])

    answer = await SqlAgentGraph(make_settings(), gateway=gateway).answer(
        "有多少客户？"
    )

    assert answer.answer == "共有 59 位客户。"
    assert gateway.calls.count("select") == 2
    assert "上一次输出无效" in gateway.selection_contexts[1]
    assert "missing" in gateway.selection_contexts[1]


async def test_rejects_table_selection_after_two_invalid_attempts() -> None:
    gateway = FakeGateway(selections=[[], ["missing"]])

    with pytest.raises(InvalidTableSelection):
        await SqlAgentGraph(make_settings(), gateway=gateway).answer("有多少客户？")

    assert gateway.calls == ["select", "select"]


async def test_rewrites_invalid_sql_then_revalidates() -> None:
    # Schema 错误允许模型修复，修复后的 SQL 必须再次经过校验。
    gateway = FakeGateway(
        "SELECT MissingColumn FROM customers",
        rewrites=["SELECT COUNT(*) FROM customers"],
    )

    answer = await SqlAgentGraph(make_settings(), gateway=gateway).answer(
        "有多少客户？"
    )

    assert answer.answer == "共有 59 位客户。"
    assert gateway.calls == ["select", "generate", "rewrite", "synthesize"]
    assert gateway.rewrite_histories == [
        ["SELECT MissingColumn FROM customers"]
    ]


async def test_rewrites_repairable_execution_error() -> None:
    # 即使预检成功，执行期可修复错误也应回到改写节点。
    gateway = FakeGateway(rewrites=["SELECT COUNT(CustomerId) FROM customers"])
    executor = RepairOnceExecutor()

    answer = await SqlAgentGraph(
        make_settings(), gateway=gateway, executor=executor
    ).answer("有多少客户？")

    assert answer.answer == "共有 59 位客户。"
    assert executor.sql == [
        "SELECT COUNT(*) FROM customers",
        "SELECT COUNT(CustomerId) FROM customers",
    ]
    assert gateway.calls.count("rewrite") == 1


@pytest.mark.parametrize(
    "question",
    [
        "把所有账单删掉。",
        "把美国客户的账单金额都改成 0。",
        "新建一张表，把客户邮箱都复制进去。",
    ],
)
async def test_unsafe_requests_never_execute_or_synthesize(question: str) -> None:
    # 安全违规必须直接拒绝，不能通过改写绕过，也不能触碰执行器。
    gateway = FakeGateway()
    executor = FailingExecutor(AssertionError("unsafe SQL reached executor"))

    answer = await SqlAgentGraph(
        make_settings(), gateway=gateway, executor=executor
    ).answer(question)

    assert "拒绝" in answer.answer
    assert executor.calls == 0
    assert "rewrite" not in gateway.calls
    assert "synthesize" not in gateway.calls


async def test_stops_when_rewrite_budget_is_exhausted() -> None:
    # max_rewrites=0 表示首次校验失败后立即终止。
    gateway = FakeGateway("SELECT MissingColumn FROM customers")

    with pytest.raises(QueryGenerationFailed, match="改写次数耗尽"):
        await SqlAgentGraph(
            make_settings(max_rewrites=0), gateway=gateway
        ).answer("有多少客户？")

    assert "rewrite" not in gateway.calls


async def test_stops_when_model_repeats_failed_sql() -> None:
    # 忽略首尾空白比较，防止模型以格式变化重复无效 SQL。
    sql = "SELECT MissingColumn FROM customers"
    gateway = FakeGateway(sql, rewrites=[f"  {sql}  "])

    with pytest.raises(QueryGenerationFailed, match="重复了失败 SQL"):
        await SqlAgentGraph(make_settings(), gateway=gateway).answer("有多少客户？")


@pytest.mark.parametrize(
    ("executor_error", "workflow_error"),
    [
        (QueryTimeoutError("interrupted"), QueryTimedOut),
        (QuerySafetyError("not authorized"), UnsafeExecutionBlocked),
    ],
)
async def test_maps_executor_failures_to_public_workflow_errors(
    executor_error: Exception, workflow_error: type[WorkflowError]
) -> None:
    # 底层执行异常不能直接泄露，应转换为工作流定义的稳定异常类型。
    executor = FailingExecutor(executor_error)

    with pytest.raises(workflow_error):
        await SqlAgentGraph(
            make_settings(), gateway=FakeGateway(), executor=executor
        ).answer("有多少客户？")


async def test_rejects_blank_question_before_calling_dependencies() -> None:
    gateway = FakeGateway()

    with pytest.raises(WorkflowError, match="问题不能为空"):
        await SqlAgentGraph(make_settings(), gateway=gateway).answer(" \n ")

    assert gateway.calls == []


@pytest.mark.parametrize(
    ("tables", "expected"),
    [
        (["customers", "customers", "invoices"], ["customers", "invoices"]),
        ([], []),
        (["missing"], []),
        (
            [
                "customers",
                "invoices",
                "tracks",
                "albums",
                "artists",
                "genres",
                "employees",
            ],
            [],
        ),
    ],
)
def test_normalizes_table_selection(
    tables: list[str], expected: list[str]
) -> None:
    # 选表结果需去重，并整体拒绝未知表、空列表或超过六张表的结果。
    available = {
        name: object()
        for name in [
            "customers",
            "invoices",
            "tracks",
            "albums",
            "artists",
            "genres",
            "employees",
        ]
    }

    assert SqlAgentGraph._normalize_selection(
        TableSelection(tables=tables), available
    ) == expected
