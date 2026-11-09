"""LangGraph 显式图编排：选表 → 生成 → 校验 → 执行 → 修复 → 汇总 / 兜底。

与 week12 的 LangChain 版本节点一一对应，区别是：
- 流程由 `StateGraph` + 条件边表达，而不是一个手写 for 循环；
- 每个节点把执行过程写入 `trace`，供 API / 前端可视化；
- `repair_attempts` 由条件边与预算控制，不存在无限循环。
"""

from __future__ import annotations

import json
import logging
import operator
import re
import time
from dataclasses import dataclass
from typing import Annotated, Any, TypedDict
from uuid import uuid4

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .catalog import SchemaCatalog, get_catalog
from .config import settings
from .executor import SQLExecutionError, SQLExecutor
from .models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    TableSelection,
    ValidationResult,
    WorkflowOutput,
)
from .providers import LLMProvider, build_provider
from .validator import SQLValidator

logger = logging.getLogger(__name__)


class AgentState(TypedDict, total=False):
    """图状态。`trace` 使用累加 reducer，其余字段由节点覆盖写入。"""

    question: str
    context: str
    history: list[dict]
    tables: list[str]
    schema_context: str
    sql: str
    attempts: list[SQLAttempt]
    validation: ValidationResult | None
    execution: ExecutionResult | None
    selection: TableSelection | None
    selection_retries: int
    selection_feedback: str
    repair_attempts: int
    answer: FinalAnswer | None
    error: str | None
    failure_message: str
    cannot_fix: bool
    clarification_answer: str
    clarification_options: list[str]
    trace: Annotated[list[dict], operator.add]


def _preview(text: str, limit: int = 500) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _code(value) -> str | None:
    return value.value if value is not None else None


def _trace(
    node: str, title: str, status: str, started: float, detail: dict
) -> dict:
    return {
        "node": node,
        "title": title,
        "status": status,
        "elapsed_ms": int((time.perf_counter() - started) * 1000),
        "detail": detail,
    }


class ThreadNotFoundError(LookupError):
    """恢复一个不存在、已完成或已被清理的会话。"""


@dataclass
class AgentRun:
    """一次图执行的结果。

    - `status=completed`：流程走完，`output` 是最终答案；
    - `status=interrupted`：clarify 节点触发了 HITL 中断，`interrupt` 给出
      澄清问题与选项，调用方拿到用户回答后调用 `resume_run` 继续。
    """

    thread_id: str
    status: str
    output: WorkflowOutput
    trace: list[dict]
    execution: ExecutionResult | None = None
    interrupt: dict | None = None

    @property
    def interrupted(self) -> bool:
        return self.status == "interrupted"


class SQLAgentGraph:
    """把一次问答编译成一张 LangGraph 图；CLI 与 API 共用同一个类。"""

    def __init__(
        self,
        catalog: SchemaCatalog | None = None,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
        *,
        max_repair_attempts: int | None = None,
        max_selection_attempts: int = 2,
        mock: bool = False,
        mock_scenario: str = "valid",
        provider: LLMProvider | None = None,
        checkpointer: MemorySaver | None = None,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.validator = validator or SQLValidator(self.catalog.db_path)
        self.executor = executor or SQLExecutor(self.catalog.db_path)
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        self.max_selection_attempts = max_selection_attempts
        self.provider = provider or build_provider(
            mock=mock or settings.mock, scenario=mock_scenario
        )
        # HITL 依赖 checkpointer：clarify 中断后，resume 从同一 thread 的检查点继续。
        self.checkpointer = checkpointer or MemorySaver()
        self.graph = self._build_graph()

    # ------------------------------------------------------------------ graph
    def _build_graph(self):
        builder = StateGraph(AgentState)
        builder.add_node("select_tables", self._select_tables)
        builder.add_node("generate_sql", self._generate_sql)
        builder.add_node("validate_sql", self._validate_sql)
        builder.add_node("execute_sql", self._execute_sql)
        builder.add_node("repair_sql", self._repair_sql)
        builder.add_node("summarize", self._summarize)
        builder.add_node("clarify", self._clarify)
        builder.add_node("fail", self._fail)

        builder.add_conditional_edges(
            START,
            self._route_entry,
            {"select_tables": "select_tables", "validate_sql": "validate_sql"},
        )
        builder.add_conditional_edges(
            "select_tables",
            self._route_after_select,
            {
                "select_tables": "select_tables",
                "generate_sql": "generate_sql",
                "clarify": "clarify",
                "fail": "fail",
            },
        )
        builder.add_edge("generate_sql", "validate_sql")
        builder.add_conditional_edges(
            "validate_sql",
            self._route_after_validation,
            {"execute_sql": "execute_sql", "repair_sql": "repair_sql", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "execute_sql",
            self._route_after_execution,
            {"summarize": "summarize", "repair_sql": "repair_sql", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "repair_sql",
            self._route_after_repair,
            {"validate_sql": "validate_sql", "fail": "fail"},
        )
        builder.add_conditional_edges(
            "clarify",
            self._route_after_clarify,
            {"generate_sql": "generate_sql", "end": END},
        )
        builder.add_edge("summarize", END)
        builder.add_edge("fail", END)
        return builder.compile(checkpointer=self.checkpointer)

    # ----------------------------------------------------------------- routing
    def _route_entry(self, state: AgentState) -> str:
        return "validate_sql" if state.get("sql") else "select_tables"

    def _route_after_select(self, state: AgentState) -> str:
        selection = state.get("selection")
        if selection is not None and selection.needs_clarification:
            return "clarify"
        problems = self._selection_problems(selection.tables if selection else [])
        if not problems:
            return "generate_sql"
        if state.get("selection_retries", 0) < self.max_selection_attempts:
            return "select_tables"
        return "fail"

    def _route_after_validation(self, state: AgentState) -> str:
        validation = state.get("validation")
        if validation is not None and validation.ok:
            return "execute_sql"
        return self._repair_or_fail(state)

    def _route_after_execution(self, state: AgentState) -> str:
        return "summarize" if state.get("execution") is not None else self._repair_or_fail(state)

    def _route_after_repair(self, state: AgentState) -> str:
        return "fail" if state.get("cannot_fix") else "validate_sql"

    def _route_after_clarify(self, state: AgentState) -> str:
        """拿到人工澄清后直接生成 SQL；未拿到则结束。"""
        return "generate_sql" if (state.get("clarification_answer") or "").strip() else "end"

    def _repair_or_fail(self, state: AgentState) -> str:
        if state.get("repair_attempts", 0) < self.max_repair_attempts:
            return "repair_sql"
        return "fail"

    # -------------------------------------------------------------------- nodes
    async def _select_tables(self, state: AgentState) -> dict:
        started = time.perf_counter()
        attempt = state.get("selection_retries", 0) + 1
        selection = await self.provider.select_tables(
            question=state.get("context", state["question"]),
            catalog_text=self.catalog.render_catalog(),
            feedback=state.get("selection_feedback", ""),
        )
        problems = self._selection_problems(selection.tables)
        logger.info(
            "node=select_tables attempt=%d tables=%s problems=%s",
            attempt,
            selection.tables,
            problems,
        )

        updates: dict[str, Any] = {
            "selection": selection,
            "selection_retries": attempt,
            "failure_message": "",
        }
        if problems:
            feedback = "；".join(problems)
            updates["selection_feedback"] = feedback
            updates["failure_message"] = f"无法确定有效的查询表：{feedback}"
            updates["tables"] = []
            status = "error"
        else:
            updates["tables"] = selection.tables
            updates["selection_feedback"] = ""
            status = "warn" if selection.needs_clarification else "ok"

        updates["trace"] = [
            _trace(
                "select_tables",
                "选择数据表",
                status,
                started,
                {
                    "attempt": attempt,
                    "tables": selection.tables,
                    "confidence": selection.confidence,
                    "reason": selection.reason,
                    "needs_clarification": selection.needs_clarification,
                    "clarification_question": selection.clarification_question,
                    "clarification_options": selection.clarification_options,
                    "problems": problems,
                },
            )
        ]
        return updates

    async def _generate_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        schema = self.catalog.render_schema(state.get("tables") or [])
        question = state.get("context", state["question"])
        clarification = (state.get("clarification_answer") or "").strip()
        if clarification:
            question += (
                "\n\n用户已对歧义做出澄清，生成 SQL 时必须严格按此口径：\n"
                f"{clarification}"
            )
        draft = await self.provider.generate_sql(question=question, schema=schema)
        logger.info("node=generate_sql sql=%s", _preview(draft.sql))
        return {
            "schema_context": schema,
            "sql": draft.sql,
            "attempts": [SQLAttempt(sql=draft.sql, source="generator")],
            "trace": [
                _trace(
                    "generate_sql",
                    "生成 SQL",
                    "ok",
                    started,
                    {
                        "sql": draft.sql,
                        "explanation": draft.explanation,
                        "assumptions": draft.assumptions,
                        "provider": self.provider.name,
                    },
                )
            ],
        }

    def _ensure_attempt(self, state: AgentState) -> list[SQLAttempt]:
        attempts = list(state.get("attempts") or [])
        sql = state.get("sql") or ""
        if not attempts:
            attempts.append(SQLAttempt(sql=sql, source="provided"))
        return attempts

    async def _validate_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        sql = state.get("sql") or ""
        attempts = self._ensure_attempt(state)
        validation = self.validator.validate(sql)
        attempts[-1] = attempts[-1].model_copy(
            update={"code": validation.code, "error": validation.message}
        )
        normalized = validation.normalized_sql if validation.ok else sql
        logger.info(
            "node=validate_sql ok=%s code=%s message=%s",
            validation.ok,
            _code(validation.code),
            _preview(validation.message),
        )
        return {
            "validation": validation,
            "sql": normalized,
            "attempts": attempts,
            "trace": [
                _trace(
                    "validate_sql",
                    "校验 SQL",
                    "ok" if validation.ok else "error",
                    started,
                    {
                        "ok": validation.ok,
                        "code": _code(validation.code),
                        "message": validation.message,
                        "sql": normalized,
                    },
                )
            ],
        }

    async def _execute_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        validation = state.get("validation")
        sql = validation.normalized_sql if validation and validation.normalized_sql else state.get("sql", "")
        attempts = self._ensure_attempt(state)
        try:
            execution = self.executor.execute(sql)
        except SQLExecutionError as exc:
            failed = ValidationResult(
                ok=False, code=exc.code, message=str(exc), normalized_sql=sql
            )
            attempts[-1] = attempts[-1].model_copy(
                update={"code": exc.code, "error": str(exc)}
            )
            logger.warning("node=execute_sql ok=false code=%s", _code(exc.code))
            return {
                "execution": None,
                "validation": failed,
                "attempts": attempts,
                "trace": [
                    _trace(
                        "execute_sql",
                        "执行 SQL",
                        "error",
                        started,
                        {"ok": False, "code": _code(exc.code), "message": str(exc)},
                    )
                ],
            }

        logger.info(
            "node=execute_sql ok=true rows=%d truncated=%s",
            execution.row_count,
            execution.truncated,
        )
        return {
            "execution": execution,
            "sql": sql,
            "attempts": attempts,
            "trace": [
                _trace(
                    "execute_sql",
                    "执行 SQL",
                    "ok",
                    started,
                    {
                        "ok": True,
                        "row_count": execution.row_count,
                        "truncated": execution.truncated,
                        "columns": execution.columns,
                    },
                )
            ],
        }

    async def _repair_sql(self, state: AgentState) -> dict:
        started = time.perf_counter()
        validation = state.get("validation")
        if validation is None:
            validation = ValidationResult(ok=False, message="未知错误", normalized_sql=state.get("sql", ""))
        attempt_number = state.get("repair_attempts", 0) + 1
        draft = await self.provider.rewrite_sql(
            question=state.get("context", state["question"]),
            schema=state.get("schema_context")
            or self.catalog.render_schema(state.get("tables") or []),
            sql=state.get("sql", ""),
            validation=validation,
            attempts=list(state.get("attempts") or []),
            attempt_number=attempt_number,
            max_attempts=self.max_repair_attempts,
        )

        if draft.cannot_fix:
            logger.warning("node=repair_sql cannot_fix reason=%s", _preview(draft.explanation))
            return {
                "cannot_fix": True,
                "failure_message": f"当前数据库无法回答该问题：{draft.explanation}",
                "error": validation.message,
                "trace": [
                    _trace(
                        "repair_sql",
                        "修复 SQL",
                        "warn",
                        started,
                        {
                            "attempt": attempt_number,
                            "cannot_fix": True,
                            "reason": draft.explanation,
                        },
                    )
                ],
            }

        attempts = list(state.get("attempts") or [])
        attempts.append(SQLAttempt(sql=draft.sql, source="rewriter"))
        logger.info(
            "node=repair_sql attempt=%d/%d sql=%s",
            attempt_number,
            self.max_repair_attempts,
            _preview(draft.sql),
        )
        return {
            "cannot_fix": False,
            "sql": draft.sql,
            "attempts": attempts,
            "repair_attempts": attempt_number,
            "failure_message": "",
            "trace": [
                _trace(
                    "repair_sql",
                    "修复 SQL",
                    "ok",
                    started,
                    {
                        "attempt": attempt_number,
                        "max_attempts": self.max_repair_attempts,
                        "sql": draft.sql,
                        "explanation": draft.explanation,
                        "cannot_fix": False,
                    },
                )
            ],
        }

    async def _summarize(self, state: AgentState) -> dict:
        started = time.perf_counter()
        execution = state.get("execution") or ExecutionResult()
        sql = state.get("sql", "")
        fallback = False
        if execution.row_count == 0:
            final = FinalAnswer(
                answer="未查询到符合条件的数据。",
                caveats=["SQL 执行成功，但结果集为空。"],
            )
        else:
            try:
                final = await self.provider.summarize(
                    question=state.get("context", state["question"]),
                    sql=sql,
                    execution=execution,
                )
            except Exception as exc:  # noqa: BLE001 - 汇总失败时降级，不影响查询结果
                logger.warning("node=summarize fallback=table error=%s", exc)
                final = self._fallback_summary(execution, exc)
                fallback = True

        logger.info(
            "node=summarize answer=%s evidence=%d caveats=%d",
            _preview(final.answer),
            len(final.evidence),
            len(final.caveats),
        )
        return {
            "answer": final,
            "trace": [
                _trace(
                    "summarize",
                    "汇总答案",
                    "warn" if fallback else "ok",
                    started,
                    {
                        "answer": final.answer,
                        "evidence": final.evidence,
                        "caveats": final.caveats,
                        "row_count": execution.row_count,
                        "truncated": execution.truncated,
                        "fallback": fallback,
                        "provider": self.provider.name,
                    },
                )
            ],
        }

    async def _clarify(self, state: AgentState) -> dict:
        """HITL 中断点：把歧义抛给用户，resume 后带着回答直接去生成 SQL。"""
        started = time.perf_counter()
        selection = state.get("selection")
        question = (
            selection.clarification_question
            if selection and selection.clarification_question
            else "无法从当前数据库确定回答该问题所需的表。"
        )
        options = [
            str(item).strip()
            for item in (selection.clarification_options if selection else [])
            if str(item).strip()
        ]
        payload = {"node": "clarify", "question": question, "options": options}
        logger.info("node=clarify waiting question=%s options=%s", _preview(question), options)
        answer = interrupt(payload)
        text = str(answer).strip()
        logger.info("node=clarify resumed answer=%s", _preview(text))
        return {
            "clarification_answer": text,
            "clarification_options": options,
            "failure_message": "",
            "trace": [
                _trace(
                    "clarify",
                    "人工澄清",
                    "ok",
                    started,
                    {
                        "question": question,
                        "options": options,
                        "answer": text,
                        "tables": selection.tables if selection else [],
                    },
                )
            ],
        }

    async def _fail(self, state: AgentState) -> dict:
        started = time.perf_counter()
        validation = state.get("validation")
        message = state.get("failure_message") or ""
        if not message:
            if validation is not None and validation.message:
                message = "无法生成可执行的 SQL，已达到最大修复次数。"
            else:
                message = "无法生成可执行的 SQL。"
        error = state.get("error") or (
            validation.message if validation is not None else message
        )
        logger.warning("node=fail message=%s", _preview(message))
        return {
            "answer": FinalAnswer(answer=message),
            "error": error,
            "trace": [
                _trace(
                    "fail",
                    "失败兜底",
                    "error",
                    started,
                    {
                        "message": message,
                        "error": error,
                        "sql": state.get("sql"),
                        "repair_attempts": state.get("repair_attempts", 0),
                    },
                )
            ],
        }

    # ------------------------------------------------------------------ helpers
    def _selection_problems(self, tables: list[str]) -> list[str]:
        if not tables:
            return ["未选择任何表"]
        unknown = sorted(set(tables) - set(self.catalog.table_names))
        if unknown:
            return [
                f"表不存在：{', '.join(unknown)}；"
                f"可选表：{', '.join(self.catalog.table_names)}"
            ]
        if not self.catalog.is_connected(tables):
            return ["所选表之间没有外键连接，请重新选择可以 JOIN 的表"]
        return []

    def _fallback_summary(self, execution: ExecutionResult, error: Exception) -> FinalAnswer:
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
            caveats=[f"结果汇总链路调用失败，已降级为表格输出：{error}"],
        )

    def _infer_tables(self, sql: str) -> list[str]:
        lowered = sql.lower()
        return [
            name
            for name in self.catalog.table_names
            if re.search(rf"\b{re.escape(name.lower())}\b", lowered)
        ]

    def _question_context(self, question: str, history: list[dict] | None) -> str:
        if not history:
            return question
        lines: list[str] = []
        for item in history[-3:]:
            prior_q = str(item.get("question", "")).strip()
            prior_a = str(item.get("answer", "")).strip()
            if not prior_q:
                continue
            lines.append(f"- 问：{prior_q}")
            if prior_a:
                lines.append(f"  答：{_preview(prior_a, 300)}")
        if not lines:
            return question
        return (
            "之前的对话（仅作为理解当前问题的上下文，不要重复回答）：\n"
            + "\n".join(lines)
            + f"\n\n当前问题：\n{question}"
        )

    # ------------------------------------------------------------------ public
    def _config(self, thread_id: str) -> dict:
        return {"recursion_limit": 60, "configurable": {"thread_id": thread_id}}

    async def start_run(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        sql: str | None = None,
        tables: list[str] | None = None,
        thread_id: str | None = None,
    ) -> AgentRun:
        """启动一次图执行；命中 clarify 时返回 `status=interrupted`，等待人工输入。"""
        if not isinstance(question, str):
            raise TypeError("question 必须是字符串")
        question = question.strip()
        thread = thread_id or uuid4().hex
        if not question:
            return AgentRun(
                thread_id=thread,
                status="completed",
                output=WorkflowOutput(question=question, answer="请输入要查询的问题。"),
                trace=[],
            )

        started = time.perf_counter()
        state: AgentState = {
            "question": question,
            "context": self._question_context(question, history),
            "history": history or [],
            "tables": tables or [],
            "attempts": [],
            "selection_retries": 0,
            "repair_attempts": 0,
            "clarification_answer": "",
            "clarification_options": [],
            "trace": [],
        }
        if sql:
            state["sql"] = sql
            state["tables"] = tables or self._infer_tables(sql)
            state["attempts"] = [SQLAttempt(sql=sql, source="provided")]
        if tables:
            state["tables"] = tables

        try:
            final_state = await self.graph.ainvoke(state, config=self._config(thread))
        except Exception as exc:  # noqa: BLE001 - CLI/API 需要稳定错误输出
            logger.exception("LangGraph 执行失败")
            return AgentRun(
                thread_id=thread,
                status="completed",
                output=WorkflowOutput(
                    question=question,
                    answer=f"处理失败：{exc}",
                    sql=state.get("sql"),
                    tables=state.get("tables") or [],
                    error=str(exc),
                ),
                trace=[],
            )

        run = self._to_run(final_state, question, thread)
        self._log_run(run, time.perf_counter() - started)
        return run

    async def resume_run(self, answer: str, *, thread_id: str) -> AgentRun:
        """用人工澄清回答恢复线程，直到图执行到 END。"""
        if not thread_id:
            raise ThreadNotFoundError("缺少 thread_id")
        text = str(answer).strip()
        if not text:
            raise ValueError("澄清回答不能为空")

        snapshot = await self.graph.aget_state(self._config(thread_id))
        if not snapshot.values or not snapshot.next:
            raise ThreadNotFoundError(f"会话不存在或已结束：{thread_id}")
        question = str(snapshot.values.get("question", ""))

        started = time.perf_counter()
        final_state = await self.graph.ainvoke(
            Command(resume=text), config=self._config(thread_id)
        )
        run = self._to_run(final_state, question, thread_id)
        self._log_run(run, time.perf_counter() - started)
        return run

    async def has_pending(self, thread_id: str) -> bool:
        """线程是否存在且仍停在中断点。"""
        if not thread_id:
            return False
        snapshot = await self.graph.aget_state(self._config(thread_id))
        return bool(snapshot.values) and bool(snapshot.next)

    def delete_thread(self, thread_id: str) -> None:
        """清理检查点，避免长驻 API 的内存持续增长。"""
        try:
            self.checkpointer.delete_thread(thread_id)
        except Exception:  # noqa: BLE001 - 清理失败不影响主流程
            logger.warning("清理 thread 失败 thread_id=%s", thread_id)

    async def run_detailed(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        sql: str | None = None,
        tables: list[str] | None = None,
    ) -> tuple[WorkflowOutput, list[dict], ExecutionResult | None]:
        """兼容旧调用：返回结果、轨迹和结果集（不阻塞等待人工输入）。"""
        run = await self.start_run(
            question, history=history, sql=sql, tables=tables
        )
        return run.output, run.trace, run.execution

    async def run_with_trace(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        sql: str | None = None,
        tables: list[str] | None = None,
    ) -> tuple[WorkflowOutput, list[dict]]:
        output, trace, _ = await self.run_detailed(
            question, history=history, sql=sql, tables=tables
        )
        return output, trace

    async def run(
        self,
        question: str,
        *,
        history: list[dict] | None = None,
        sql: str | None = None,
        tables: list[str] | None = None,
    ) -> WorkflowOutput:
        output, _ = await self.run_with_trace(
            question, history=history, sql=sql, tables=tables
        )
        return output

    # ------------------------------------------------------------- run helpers
    def _interrupt_payload(self, state: dict) -> dict | None:
        raw = state.get("__interrupt__") or []
        if not raw:
            return None
        value = getattr(raw[0], "value", raw[0])
        payload = dict(value) if isinstance(value, dict) else {"question": str(value)}
        payload.setdefault("node", "clarify")
        payload.setdefault("question", "需要补充信息后才能继续查询。")
        payload.setdefault("options", [])
        return payload

    def _wait_trace(self, payload: dict) -> dict:
        detail = dict(payload)
        detail["waiting"] = True
        return {
            "node": "clarify",
            "title": "等待人工澄清",
            "status": "warn",
            "elapsed_ms": 0,
            "detail": detail,
        }

    def _to_run(self, state: dict, question: str, thread_id: str) -> AgentRun:
        payload = self._interrupt_payload(state)
        execution = state.get("execution")
        committed = list(state.get("trace") or [])
        if payload is not None:
            fallback = str(payload.get("question") or "需要补充信息后才能继续。")
            return AgentRun(
                thread_id=thread_id,
                status="interrupted",
                output=self._build_output(state, question, fallback=fallback),
                trace=committed + [self._wait_trace(payload)],
                execution=execution,
                interrupt=payload,
            )
        return AgentRun(
            thread_id=thread_id,
            status="completed",
            output=self._build_output(state, question),
            trace=committed,
            execution=execution,
        )

    def _log_run(self, run: AgentRun, elapsed: float) -> None:
        logger.info(
            "graph finished status=%s elapsed=%.2fs nodes=%d rows=%s repairs=%s error=%s",
            run.status,
            elapsed,
            len(run.trace),
            run.output.row_count,
            run.output.repair_attempts,
            run.output.error,
        )

    def _build_output(
        self, state: AgentState, question: str, *, fallback: str = "处理失败：未返回答案。"
    ) -> WorkflowOutput:
        answer = state.get("answer")
        execution = state.get("execution")
        return WorkflowOutput(
            question=question,
            answer=answer.answer if answer is not None else fallback,
            sql=state.get("sql") or None,
            tables=state.get("tables") or [],
            row_count=execution.row_count if execution is not None else 0,
            truncated=execution.truncated if execution is not None else False,
            repair_attempts=state.get("repair_attempts", 0),
            evidence=answer.evidence if answer is not None else [],
            caveats=answer.caveats if answer is not None else [],
            error=state.get("error"),
        )
