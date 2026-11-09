"""运行真实 LangGraph 与临时 SQLite，所有模型调用均使用异步替身。"""

import asyncio
import json
import logging
import sqlite3
import sys
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from langgraph.graph.state import CompiledStateGraph

import sql_agent.workflow as workflow_module
import sql_agent.cli as cli_module
from sql_agent.db.catalog import SchemaCatalog
from sql_agent.db.executor import SQLExecutionError, SQLExecutor
from sql_agent.db.validator import SQLValidator
from sql_agent.models import ErrorCode, FinalAnswer, SQLDraft, TableSelection
from sql_agent.workflow import SQLAgentWorkflow


# 固定问题和一对有效/无效 SQL，便于断言修复前后的意图与执行内容。
QUESTION = "列出歌曲名称"
VALID_SQL = "SELECT Name FROM tracks ORDER BY TrackId"
INVALID_SQL = "SELECT missing FROM tracks"


def response(value):
    """模拟 create_agent 的返回结构，让业务代码仍执行结构化类型检查。"""
    return {"structured_response": value}


def selection(tables=None, **kwargs):
    """构造选表结果；None 使用默认表，显式空列表用于测试无效选表。"""
    return TableSelection(
        tables=["tracks"] if tables is None else tables, reason="测试选表", **kwargs
    )


@pytest.fixture
def agents(monkeypatch):
    """为每个测试替换四个异步模型调用，默认沿成功路径返回，结束后自动恢复。"""
    defaults = {
        "table_selector_agent": selection(),
        "sql_generator_agent": SQLDraft(sql=VALID_SQL),
        "sql_rewriter_agent": SQLDraft(sql=VALID_SQL),
        "result_summary_agent": FinalAnswer(answer="共三首歌曲。", evidence=["A、B、C"]),
    }
    mocks = {}
    for name, value in defaults.items():
        # 替换工作流实际引用的 Agent；保留调用记录，供测试检查提示词和调用次数。
        mock = AsyncMock(return_value=response(value))
        monkeypatch.setattr(workflow_module, name, SimpleNamespace(ainvoke=mock))
        mocks[name] = mock
    return SimpleNamespace(**mocks)


@pytest.fixture
def workflow(tmp_path, agents):
    """创建独立临时数据库和真实图；依赖 agents fixture，确保图不会调用外部模型。"""
    db_path = tmp_path / "test.db"
    # tracks 与 albums 有外键，unrelated 刻意独立，用于验证连通性检查。
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE albums (AlbumId INTEGER PRIMARY KEY, Title TEXT);
            CREATE TABLE tracks (
                TrackId INTEGER PRIMARY KEY, Name TEXT,
                AlbumId INTEGER REFERENCES albums(AlbumId)
            );
            CREATE TABLE unrelated (Id INTEGER PRIMARY KEY);
            INSERT INTO albums VALUES (1, 'Album');
            INSERT INTO tracks VALUES (1, 'A', 1), (2, 'B', 1), (3, 'C', 1);
        """)
    catalog = SchemaCatalog(db_path)
    validator = SQLValidator(db_path)
    # wraps 保留真实校验/执行行为，同时记录调用，便于确认非法 SQL 未被执行。
    workflow = SQLAgentWorkflow(
        catalog=catalog,
        validator=Mock(wraps=validator),
        executor=Mock(wraps=SQLExecutor(db_path)),
        max_repair_attempts=3,
    )
    yield workflow
    # 释放连接池，避免 Windows 上打开的数据库文件影响临时目录清理。
    validator.engine.dispose()
    catalog.engine.dispose()


def run(workflow, question=QUESTION):
    """在普通 pytest 测试函数中执行异步工作流，无需额外异步测试插件。"""
    return asyncio.run(workflow.run(question))


def prompt(mock, index=-1):
    """读取指定一次模型调用的提示词，默认读取最后一次调用。"""
    return mock.call_args_list[index].args[0]["messages"][0].content


def test_success_normalizes_sql_and_preserves_output(workflow, agents):
    """确认代码围栏和分号被规范化，且最终 JSON 字段与迁移前的契约一致。"""
    agents.sql_generator_agent.return_value = response(SQLDraft(sql=f"```sql\n{VALID_SQL};\n```"))
    assert isinstance(workflow.graph, CompiledStateGraph)
    result = run(workflow, f"  {QUESTION}  ")
    assert json.loads(result.model_dump_json()) == {
        "question": QUESTION, "answer": "共三首歌曲。", "sql": VALID_SQL,
        "tables": ["tracks"], "row_count": 3, "truncated": False,
        "repair_attempts": 0, "evidence": ["A、B、C"], "caveats": [], "error": None,
    }
    workflow.executor.execute.assert_called_once_with(VALID_SQL)
    agents.sql_rewriter_agent.assert_not_awaited()


@pytest.mark.parametrize("bad_sql", [INVALID_SQL, "DELETE FROM tracks"])
def test_validation_failure_repaired_before_execution(workflow, agents, bad_sql):
    """未知字段和写操作都必须先被拒绝，再将原问题和实际错误交给修复模型。"""
    agents.sql_generator_agent.return_value = response(SQLDraft(sql=bad_sql))
    result = run(workflow)
    assert result.error is None
    assert result.repair_attempts == 1
    assert workflow.validator.validate.call_count == 2
    workflow.executor.execute.assert_called_once_with(VALID_SQL)
    rewrite_prompt = prompt(agents.sql_rewriter_agent)
    assert QUESTION in rewrite_prompt
    assert bad_sql in rewrite_prompt
    assert "[generator]" in rewrite_prompt
    expected_code = ErrorCode.UNKNOWN_OBJECT if bad_sql == INVALID_SQL else ErrorCode.UNSAFE
    assert str(expected_code) in rewrite_prompt


@pytest.mark.parametrize("code", [ErrorCode.RUNTIME, ErrorCode.TIMEOUT])
def test_execution_error_repaired_and_revalidated(workflow, agents, code):
    """模拟首次运行错误或超时，确认改写后重新校验，再执行真实查询。"""
    original_execute = workflow.executor.execute._mock_wraps
    calls = 0

    def execute(sql):
        """只让首次执行失败，使测试能够同时验证错误分支和修复后的成功分支。"""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise SQLExecutionError("模拟执行失败", code)
        return original_execute(sql)

    workflow.executor.execute.side_effect = execute
    result = run(workflow)
    assert result.error is None
    assert result.repair_attempts == 1
    assert workflow.validator.validate.call_count == 2
    assert workflow.executor.execute.call_count == 2
    assert str(code) in prompt(agents.sql_rewriter_agent)
    assert "模拟执行失败" in prompt(agents.sql_rewriter_agent)


@pytest.mark.parametrize("limit", [0, 1, 3, 12])
def test_repair_limit_stops_graph(workflow, agents, limit):
    """持续返回错误 SQL，检查零次、少量和较大修复预算都能按业务上限终止。"""
    workflow = SQLAgentWorkflow(
        workflow.catalog, workflow.validator, workflow.executor, max_repair_attempts=limit
    )
    agents.sql_generator_agent.return_value = response(SQLDraft(sql=INVALID_SQL))
    agents.sql_rewriter_agent.return_value = response(SQLDraft(sql=INVALID_SQL))
    result = run(workflow)
    assert result.error
    assert "最大修复次数" in result.answer
    assert result.repair_attempts == limit
    assert result.sql == INVALID_SQL
    assert agents.sql_rewriter_agent.await_count == limit
    assert workflow.validator.validate.call_count == limit + 1
    workflow.executor.execute.assert_not_called()
    agents.result_summary_agent.assert_not_awaited()
    if limit >= 3:
        assert "[rewriter]" in prompt(agents.sql_rewriter_agent)


@pytest.mark.parametrize("accepted_repairs", [0, 1])
def test_cannot_fix_does_not_count_as_accepted_repair(workflow, agents, accepted_repairs):
    """模型拒绝修复时立即结束，其调用不计入已接受的 SQL 改写次数。"""
    agents.sql_generator_agent.return_value = response(SQLDraft(sql=INVALID_SQL))
    agents.sql_rewriter_agent.side_effect = [
        *[response(SQLDraft(sql=INVALID_SQL)) for _ in range(accepted_repairs)],
        response(SQLDraft(sql="", cannot_fix=True, explanation="缺少所需字段")),
    ]
    result = run(workflow)
    assert result.error
    assert "缺少所需字段" in result.answer
    assert result.repair_attempts == accepted_repairs
    assert agents.sql_rewriter_agent.await_count == accepted_repairs + 1
    workflow.executor.execute.assert_not_called()


def test_explicit_clarification_is_not_an_error(workflow, agents):
    """模型主动请求澄清时不生成或校验 SQL，输出也不携带错误。"""
    agents.table_selector_agent.return_value = response(selection(
        [], needs_clarification=True, clarification_question="需要查询哪类数据？"
    ))
    result = run(workflow)
    assert result.answer == "需要查询哪类数据？"
    assert result.error is None
    agents.table_selector_agent.assert_awaited_once()
    agents.sql_generator_agent.assert_not_awaited()
    workflow.validator.validate.assert_not_called()


@pytest.mark.parametrize("tables", [[], ["missing"], ["tracks", "unrelated"]])
def test_invalid_selection_stops_after_two_attempts(workflow, agents, tables):
    """空表列表、未知表和不连通的表都最多选两次，之后返回澄清说明。"""
    agents.table_selector_agent.return_value = response(selection(tables))
    result = run(workflow)
    assert result.error is None
    assert "无法确定有效的查询表" in result.answer
    assert agents.table_selector_agent.await_count == 2
    assert "上一次选择的问题" in prompt(agents.table_selector_agent)
    agents.sql_generator_agent.assert_not_awaited()


def test_selection_retry_recovers_with_connected_tables(workflow, agents):
    """第一次选表失败的反馈应进入第二次提示词，纠正后可继续查询。"""
    agents.table_selector_agent.side_effect = [
        response(selection(["missing"])), response(selection(["tracks", "albums"])),
    ]
    result = run(workflow)
    assert result.error is None
    assert result.tables == ["tracks", "albums"]
    assert agents.table_selector_agent.await_count == 2
    assert "表不存在" in prompt(agents.table_selector_agent)


def test_empty_result_skips_summary_and_repair(workflow, agents):
    """成功执行但没有匹配行时返回固定答复，不让模型猜原因或尝试改写。"""
    agents.sql_generator_agent.return_value = response(SQLDraft(
        sql="SELECT Name FROM tracks WHERE TrackId < 0"
    ))
    result = run(workflow)
    assert result.error is None
    assert result.row_count == 0
    assert result.answer == "未查询到符合条件的数据。"
    agents.result_summary_agent.assert_not_awaited()
    agents.sql_rewriter_agent.assert_not_awaited()


@pytest.mark.parametrize("failure", [RuntimeError("汇总不可用"), "wrong-structured-type"])
def test_summary_failure_falls_back_to_actual_rows(workflow, agents, failure):
    """汇总抛异常或返回错误类型时，用真实结果生成表格，并保留成功执行状态。"""
    if isinstance(failure, Exception):
        agents.result_summary_agent.side_effect = failure
    else:
        agents.result_summary_agent.return_value = response(failure)
    result = run(workflow)
    assert result.error is None
    assert result.row_count == 3
    assert "| Name |" in result.answer
    assert "| A |" in result.answer
    assert "降级为表格输出" in result.caveats[0]
    agents.sql_rewriter_agent.assert_not_awaited()


def test_execution_and_summary_row_limits_remain_distinct(workflow, agents, monkeypatch):
    """执行保留两行、汇总展示一行时，row_count 仍为两行且保留执行器截断标记。"""
    workflow.executor = SQLExecutor(workflow.catalog.db_path, max_rows=2)
    monkeypatch.setattr(workflow_module, "settings", replace(
        workflow_module.settings, max_summary_rows=1
    ))
    agents.result_summary_agent.side_effect = RuntimeError("汇总不可用")
    result = run(workflow)
    assert result.row_count == 2
    assert result.truncated is True
    assert "| A |" in result.answer
    assert "| B |" not in result.answer
    assert "仅展示前 1 行" in result.answer
    assert '[["A"]]' in prompt(agents.result_summary_agent)


def test_reusing_graph_does_not_leak_state(workflow, agents):
    """同一工作流连续查询时，前一次的修复计数不能进入下一次查询。"""
    agents.sql_generator_agent.side_effect = [
        response(SQLDraft(sql=INVALID_SQL)), response(SQLDraft(sql=VALID_SQL)),
    ]
    first = run(workflow)
    second = run(workflow)
    assert first.repair_attempts == 1
    assert second.repair_attempts == 0
    assert first.error is second.error is None
    assert agents.sql_rewriter_agent.await_count == 1


def test_empty_question_does_not_invoke_graph(workflow, agents):
    """纯空白输入由 run() 提前处理，不进入选表节点。"""
    result = run(workflow, "  ")
    assert result.error is None
    assert result.question == ""
    agents.table_selector_agent.assert_not_awaited()


def test_agent_failure_produces_error_output(workflow, agents):
    """未被节点单独处理的模型异常由 run() 捕获，转换为稳定的错误响应。"""
    agents.sql_generator_agent.side_effect = RuntimeError("生成失败")
    result = run(workflow)
    assert result.error == "生成失败"
    workflow.executor.execute.assert_not_called()


def test_negative_repair_limit_rejected(workflow):
    """负修复预算应在创建工作流时被拒绝，不能进入图执行。"""
    with pytest.raises(ValueError, match="非负整数"):
        SQLAgentWorkflow(workflow.catalog, max_repair_attempts=-1)


def test_execution_failure_exhausts_repairs(workflow, agents):
    """持续执行超时也必须受修复预算限制，并返回最后的实际错误。"""
    workflow.executor.execute.side_effect = SQLExecutionError("持续超时", ErrorCode.TIMEOUT)
    result = run(workflow)
    assert result.error == "持续超时"
    assert result.repair_attempts == 3
    assert workflow.executor.execute.call_count == 4
    assert workflow.validator.validate.call_count == 4
    agents.result_summary_agent.assert_not_awaited()


def test_stream_exposes_repair_route_and_preserves_history_snapshots(workflow, agents):
    """观察真实节点更新顺序，并确认补写错误不会反向修改先前输出的历史快照。"""
    agents.sql_generator_agent.return_value = response(SQLDraft(sql=INVALID_SQL))

    async def collect():
        """updates 模式每次提供本节点更新的字段，适合检查路由与状态变化。"""
        return [update async for update in workflow.graph.astream(
            {"question": QUESTION}, stream_mode="updates"
        )]

    updates = asyncio.run(collect())
    assert [next(iter(update)) for update in updates] == [
        "select_tables", "generate_sql", "validate_sql", "rewrite_sql",
        "validate_sql", "execute_sql", "summarize", "finalize",
    ]
    initial_attempt = updates[1]["generate_sql"]["attempts"][0]
    failed_attempt = updates[2]["validate_sql"]["attempts"][0]
    history = updates[3]["rewrite_sql"]["attempts"]
    assert initial_attempt.error == ""
    assert failed_attempt.code == ErrorCode.UNKNOWN_OBJECT
    assert [attempt.sql for attempt in history] == [INVALID_SQL, VALID_SQL]
    assert history[0].error == failed_attempt.error
    assert history[1].error == ""


@pytest.mark.parametrize("outcome", ["success", "error", "clarification"])
def test_cli_json_exit_code_and_stderr_logging(workflow, agents, monkeypatch, capsys, outcome):
    """检查 CLI 成功、错误和澄清三条路径的 JSON、退出码及标准错误日志。"""
    if outcome == "error":
        agents.sql_generator_agent.side_effect = RuntimeError("生成失败")
    elif outcome == "clarification":
        agents.table_selector_agent.return_value = response(selection(
            [], needs_clarification=True, clarification_question="需要查询哪类数据？"
        ))
    # CLI 使用当前测试的临时库和模型替身，但仍执行真实入口、参数解析和输出逻辑。
    monkeypatch.setattr(cli_module, "SQLAgentWorkflow", lambda: workflow)
    monkeypatch.setattr(sys, "argv", ["sql_agent", QUESTION, "--log-level", "INFO"])
    root_logger = logging.getLogger()
    original_handlers = root_logger.handlers[:]
    original_level = root_logger.level
    try:
        if outcome == "error":
            with pytest.raises(SystemExit) as exc:
                cli_module.main()
            assert exc.value.code == 1
        else:
            cli_module.main()
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["question"] == QUESTION
        assert bool(payload["error"]) == (outcome == "error")
        assert "workflow started" in captured.err
    finally:
        # CLI 会重配根日志；恢复 pytest 的处理器，避免影响其他测试。
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)
            handler.close()
        root_logger.handlers = original_handlers
        root_logger.setLevel(original_level)
