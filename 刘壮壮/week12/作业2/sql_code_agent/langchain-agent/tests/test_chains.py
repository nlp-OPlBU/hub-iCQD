"""LangChain 模型网关的契约测试，不发起真实网络请求。"""

import json
from typing import Any

import pytest

from langchain_sql_agent.chains import (
    PROMPT_VERSION,
    LangChainModelGateway,
    ModelContractError,
)
from langchain_sql_agent.models import (
    FinalAnswer,
    QueryResult,
    SqlDraft,
    TableSelection,
)


class StubRunnable:
    """最小 Runnable 测试替身，用于捕获 ainvoke 的输入和追踪配置。"""

    def __init__(self, result: object) -> None:
        self.result = result
        self.inputs: dict[str, Any] | None = None
        self.config: dict[str, Any] | None = None

    async def ainvoke(
        self, inputs: dict[str, Any], config: dict[str, Any]
    ) -> object:
        self.inputs = inputs
        self.config = config
        return self.result


def gateway_without_model() -> LangChainModelGateway:
    """绕过构造函数，避免创建需要 API 配置的 ChatOpenAI。"""

    return object.__new__(LangChainModelGateway)


async def test_run_passes_trace_configuration_and_returns_typed_result() -> None:
    # 每次调用都应带上请求 ID 和 Prompt 版本，便于线上追踪。
    gateway = gateway_without_model()
    runnable = StubRunnable(TableSelection(tables=["customers"]))

    result = await gateway._run(
        runnable,
        {"question": "有多少客户？"},
        "trace_123",
        "select_tables",
        TableSelection,
    )

    assert result == TableSelection(tables=["customers"])
    assert runnable.inputs == {"question": "有多少客户？"}
    assert runnable.config == {
        "run_name": "select_tables",
        "tags": ["select_tables"],
        "metadata": {
            "request_id": "trace_123",
            "prompt_version": PROMPT_VERSION,
        },
    }


async def test_run_rejects_output_that_breaks_model_contract() -> None:
    # 即使字典内容看似正确，也必须拒绝非约定 Pydantic 类型。
    gateway = gateway_without_model()

    with pytest.raises(
        ModelContractError, match="模型输出类型错误，期望 TableSelection"
    ):
        await gateway._run(
            StubRunnable({"tables": ["customers"]}),
            {},
            "trace_123",
            "select_tables",
            TableSelection,
        )


async def test_rewrite_serializes_history_without_ascii_escaping() -> None:
    # 中文历史 SQL 应原样进入 Prompt，避免变成难以阅读的 Unicode 转义。
    gateway = gateway_without_model()
    runnable = StubRunnable(SqlDraft(sql="SELECT 1"))
    gateway.sql_rewriter = runnable

    result = await gateway.rewrite_sql(
        question="有多少客户？",
        focused_schema="{}",
        sql="SELECT 错误字段 FROM customers",
        error="没有这个字段",
        history=["SELECT 错误字段 FROM customers"],
        request_id="trace_456",
    )

    assert result.sql == "SELECT 1"
    assert runnable.inputs is not None
    assert json.loads(runnable.inputs["history"]) == [
        "SELECT 错误字段 FROM customers"
    ]
    assert "\\u" not in runnable.inputs["history"]


async def test_synthesize_forwards_query_result_to_chain() -> None:
    # 汇总链必须收到完整查询结果，不能只传行数据或字符串。
    gateway = gateway_without_model()
    runnable = StubRunnable(FinalAnswer(answer="共有 59 位客户。"))
    gateway.answer_synthesizer = runnable
    query_result = QueryResult(
        columns=["count"],
        rows=[[59]],
        row_count=1,
        truncated=False,
        elapsed_ms=1.2,
    )

    answer = await gateway.synthesize(
        "有多少客户？",
        "SELECT COUNT(*) FROM customers",
        query_result,
        "trace_789",
    )

    assert answer.answer == "共有 59 位客户。"
    assert runnable.inputs == {
        "question": "有多少客户？",
        "sql": "SELECT COUNT(*) FROM customers",
        "result": query_result,
    }
