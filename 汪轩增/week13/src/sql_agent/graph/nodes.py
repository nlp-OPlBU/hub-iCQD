"""图节点：LLM 决策节点复用 StructuredAgent，确定性节点复用 db 组件。

节点返回「部分状态更新 dict」，由 LangGraph 的 reducer 合并。LLM 节点用
``async def``（与 ``StructuredAgent.ainvoke`` 对齐）；确定性节点用 ``def``，
LangGraph 会在线程池里执行。
"""

from __future__ import annotations

import logging

from ..agents import (
    result_summary_agent,
    sql_generator_agent,
    sql_rewriter_agent,
    table_selector_agent,
)
from ..config import settings
from ..db.catalog import SchemaCatalog
from ..db.executor import SQLExecutionError, SQLExecutor
from ..db.validator import SQLValidator
from ..models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    TableSelection,
    ValidationResult,
)
from .prompts import (
    build_generate_prompt,
    build_rewrite_prompt,
    build_select_prompt,
    build_summarize_prompt,
)
from .state import GraphState

logger = logging.getLogger(__name__)


def _preview(text: object, limit: int = 500) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _fallback_summary(execution: ExecutionResult, error: Exception) -> FinalAnswer:
    """汇总 Agent 失败时的降级输出：Markdown 表格 + 行数。"""

    if not execution.rows:
        return FinalAnswer(answer="未查询到符合条件的数据。")

    header = "| " + " | ".join(execution.columns) + " |"
    divider = "| " + " | ".join("---" for _ in execution.columns) + " |"
    body = [
        "| " + " | ".join(value.replace("|", "\\|") for value in row) + " |"
        for row in execution.rows[: settings.max_summary_rows]
    ]
    note = (
        f"仅展示前 {settings.max_summary_rows} 行。"
        if execution.truncated or execution.row_count > settings.max_summary_rows
        else ""
    )
    return FinalAnswer(
        answer="\n".join(
            [f"查询返回 {execution.row_count} 行。", header, divider, *body, note]
        ),
        caveats=[f"结果汇总 Agent 调用失败，已降级为表格输出：{error}"],
    )


def make_nodes(
    *,
    catalog: SchemaCatalog,
    validator: SQLValidator,
    executor: SQLExecutor,
) -> dict[str, object]:
    """构造节点名 -> 节点函数 的映射，闭包持有确定性组件。"""

    def _selection_problems(tables: list[str]) -> list[str]:
        if not tables:
            return ["未选择任何表"]
        unknown = sorted(set(tables) - set(catalog.table_names))
        if unknown:
            return [
                f"表不存在：{', '.join(unknown)}；"
                f"可选表：{', '.join(catalog.table_names)}"
            ]
        if not catalog.is_connected(tables):
            return ["所选表之间没有外键连接，请重新选择可以 JOIN 的表"]
        return []

    async def select_tables(state: GraphState) -> dict:
        question = state["question"]
        catalog_text = catalog.render_catalog()
        feedback = ""
        selection: TableSelection | None = None

        # 与现有 workflow 一致：非法表/不连通时反馈重试，最多 2 次。
        for _attempt in range(1, 3):
            prompt = build_select_prompt(question, catalog_text, feedback)
            selection = await table_selector_agent.ainvoke(prompt)
            if selection.needs_clarification:
                break
            problems = _selection_problems(selection.tables)
            if not problems:
                break
            feedback = "；".join(problems)

        if selection is None:
            selection = TableSelection(
                tables=[],
                needs_clarification=True,
                clarification_question="无法确定有效的查询表。",
            )
        elif not selection.needs_clarification and _selection_problems(
            selection.tables
        ):
            selection = selection.model_copy(
                update={
                    "needs_clarification": True,
                    "clarification_question": f"无法确定有效的查询表：{feedback}",
                }
            )

        schema_context = ""
        if selection.tables and not selection.needs_clarification:
            schema_context = catalog.render_schema(selection.tables)

        logger.info(
            "stage=table_select tables=%s needs_clarification=%s",
            selection.tables,
            selection.needs_clarification,
        )
        return {"selection": selection, "schema_context": schema_context}

    async def generate_sql(state: GraphState) -> dict:
        draft = await sql_generator_agent.ainvoke(
            build_generate_prompt(state["question"], state["schema_context"])
        )
        logger.info("stage=sql_generated sql=%s", _preview(draft.sql))
        return {
            "sql": draft.sql,
            "attempts": [SQLAttempt(sql=draft.sql, source="generator")],
        }

    def validate(state: GraphState) -> dict:
        result = validator.validate(state["sql"])
        logger.info(
            "stage=validate ok=%s code=%s message=%s",
            result.ok,
            result.code,
            _preview(result.message),
        )
        return {"validation": result}

    def execute(state: GraphState) -> dict:
        validation = state["validation"]
        try:
            execution = executor.execute(validation.normalized_sql)
            logger.info(
                "stage=execute ok=true rows=%s truncated=%s",
                execution.row_count,
                execution.truncated,
            )
            return {"execution": execution, "sql": validation.normalized_sql}
        except SQLExecutionError as exc:
            logger.warning("stage=execute ok=false code=%s message=%s", exc.code, exc)
            return {
                "validation": ValidationResult(
                    ok=False,
                    code=exc.code,
                    message=str(exc),
                    normalized_sql=state["sql"],
                )
            }

    async def rewrite(state: GraphState) -> dict:
        validation = state["validation"]
        attempt_number = state.get("repair_attempts", 0) + 1
        max_attempts = state.get("max_repair_attempts", 0)
        draft = await sql_rewriter_agent.ainvoke(
            build_rewrite_prompt(
                question=state["question"],
                schema=state["schema_context"],
                sql=state["sql"],
                validation=validation,
                attempts=state.get("attempts", []),
                attempt_number=attempt_number,
                max_attempts=max_attempts,
            )
        )
        if draft.cannot_fix:
            logger.warning(
                "stage=rewrite_cannot_fix reason=%s", _preview(draft.explanation)
            )
            return {
                "cannot_fix": True,
                "cannot_fix_reason": draft.explanation,
                "error": validation.message if validation else "",
            }
        logger.info(
            "stage=sql_rewritten attempt=%d sql=%s", attempt_number, _preview(draft.sql)
        )
        return {
            "sql": draft.sql,
            "repair_attempts": state.get("repair_attempts", 0) + 1,
            "attempts": [SQLAttempt(sql=draft.sql, source="rewriter")],
        }

    async def summarize(state: GraphState) -> dict:
        execution = state["execution"]
        # 空结果不重试，也不让模型猜测原因。
        if execution.row_count == 0:
            logger.info("stage=summarize skipped reason=empty_result")
            final = FinalAnswer(
                answer="未查询到符合条件的数据。",
                caveats=["SQL 执行成功，但结果集为空。"],
            )
        else:
            try:
                final = await result_summary_agent.ainvoke(
                    build_summarize_prompt(
                        state["question"], state["sql"], execution
                    )
                )
            except Exception as exc:  # noqa: BLE001 - 汇总失败时降级输出
                logger.warning("stage=summarize_failed fallback=table error=%s", exc)
                final = _fallback_summary(execution, exc)
        logger.info("stage=final answer=%s", _preview(final.answer))
        return {"answer": final, "outcome": "done"}

    def clarify(state: GraphState) -> dict:
        selection = state.get("selection")
        answer = (
            selection.clarification_question if selection else None
        ) or "无法从当前数据库确定回答该问题所需的表。"
        logger.warning("stage=clarify question=%s", answer)
        return {"outcome": "clarify", "answer": FinalAnswer(answer=answer)}

    def fail(state: GraphState) -> dict:
        if state.get("cannot_fix"):
            answer = f"当前数据库无法回答该问题：{state.get('cannot_fix_reason', '')}"
        else:
            answer = "无法生成可执行的 SQL，已达到最大修复次数。"
        validation = state.get("validation")
        logger.warning(
            "stage=fail cannot_fix=%s answer=%s", state.get("cannot_fix"), answer
        )
        return {
            "outcome": "fail",
            "answer": FinalAnswer(answer=answer),
            "error": validation.message if validation else "unknown error",
        }

    return {
        "select_tables": select_tables,
        "generate_sql": generate_sql,
        "validate": validate,
        "execute": execute,
        "rewrite": rewrite,
        "summarize": summarize,
        "clarify": clarify,
        "fail": fail,
    }
