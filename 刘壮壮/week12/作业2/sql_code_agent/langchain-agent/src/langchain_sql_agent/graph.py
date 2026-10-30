import uuid
import logging
import json
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .chains import LangChainModelGateway, ModelGateway
from .config import Settings
from .models import (
    FinalAnswer,
    QueryResult,
    SchemaCatalog,
    TableSelection,
    ValidationResult,
)
from .query_executor import (
    QueryExecutor,
    QuerySafetyError,
    QueryTimeoutError,
    ReadOnlyQueryExecutor,
    RepairableQueryError,
)
from .schema_service import SchemaService
from .sql_safety import SqlSafetyValidator

logger = logging.getLogger("sql_agent_lc")


class SqlAgentState(TypedDict, total=False):
    request_id: str
    question: str
    catalog: SchemaCatalog
    selection_context: str
    selected_tables: list[str]
    focused_schema: str
    sql: str
    validation: ValidationResult
    query_result: QueryResult
    rewrite_count: int
    failed_sql: list[str]
    final_answer: str
    refusal_reason: str
    error_code: Literal[
        "InvalidTableSelection",
        "ModelContractError",
        "QueryGenerationFailed",
        "QueryTimedOut",
        "UnsafeExecutionBlocked",
    ]


class WorkflowError(RuntimeError):
    public_message = "系统暂时无法完成查询。"


class InvalidTableSelection(WorkflowError):
    public_message = "无法为该问题选择有效的数据表。"


class QueryGenerationFailed(WorkflowError):
    public_message = "无法形成有效查询。"


class QueryTimedOut(WorkflowError):
    public_message = "查询超过资源限制。"


class UnsafeExecutionBlocked(WorkflowError):
    public_message = "该请求不是安全的只读查询，已拒绝执行。"


class SqlAgentGraph:
    def __init__(
        self,
        settings: Settings,
        gateway: ModelGateway | None = None,
        schema_service: SchemaService | None = None,
        validator: SqlSafetyValidator | None = None,
        executor: QueryExecutor | None = None,
    ) -> None:
        self.settings = settings
        self.gateway = gateway or LangChainModelGateway(settings)
        self.schema_service = schema_service or SchemaService(settings.db_path)
        self.validator = validator or SqlSafetyValidator(settings.db_path)
        self.executor = executor or ReadOnlyQueryExecutor(
            settings.db_path,
            max_result_rows=settings.max_result_rows,
            max_progress_steps=settings.max_progress_steps,
        )

    async def answer(self, question: str) -> FinalAnswer:
        question = question.strip()
        if not question:
            raise WorkflowError("问题不能为空")
        request_id = f"trace_{uuid.uuid4().hex}"
        self._log(request_id, "request_started")

        workflow = StateGraph(SqlAgentState)

        def parse_database(_: SqlAgentState) -> dict[str, object]:
            catalog = self.schema_service.load()
            self._log(
                request_id, "database_parsed", table_count=len(catalog.tables)
            )
            return {
                "catalog": catalog,
                "selection_context": self.schema_service.selection_context(catalog),
            }

        async def select_tables(state: SqlAgentState) -> dict[str, object]:
            selection_context = state["selection_context"]
            catalog = state["catalog"]

            for attempt in range(2):
                candidate = await self.gateway.select_tables(
                    state["question"], selection_context, state["request_id"]
                )
                normalized = self._normalize_selection(
                    candidate, catalog.tables
                )
                if normalized:
                    focused_schema = self.schema_service.focus(
                        normalized, catalog
                    )
                    self._log(
                        request_id, "tables_selected", tables=normalized
                    )
                    return {
                        "selected_tables": normalized,
                        "focused_schema": focused_schema,
                    }

                self._log(
                    request_id, "table_selection_retry", attempt=attempt + 1
                )
                selection_context += (
                    "\n\n上一次输出无效。必须只返回上述目录中的 1 到 6 个精确表名；"
                    f"无效输出为：{candidate.tables}"
                )

            raise InvalidTableSelection()

        async def generate_sql(state: SqlAgentState) -> dict[str, object]:
            draft = await self.gateway.generate_sql(
                state["question"],
                state["focused_schema"],
                state["request_id"],
            )
            self._log(request_id, "sql_generated", sql=draft.sql)
            return {"sql": draft.sql}

        def validate_sql(state: SqlAgentState) -> dict[str, object]:
            validation = self.validator.validate(
                state["sql"], state["question"]
            )
            self._log(
                request_id,
                "sql_validated",
                category=validation.category,
                read_tables=validation.read_tables,
            )
            return {"validation": validation}

        def route_after_validation(
            state: SqlAgentState,
        ) -> Literal["refuse", "execute_sql", "rewrite_sql"]:
            if state["validation"].category in {
                "SAFETY_VIOLATION",
                "MULTI_STATEMENT",
            }:
                return "refuse"
            if state["validation"].is_valid:
                return "execute_sql"
            return "rewrite_sql"

        def execute_sql(state: SqlAgentState) -> dict[str, object]:
            try:
                result = self.executor.execute(state["sql"])
            except RepairableQueryError as error:
                return {
                    "validation": ValidationResult(
                        is_valid=False,
                        category="SYNTAX_ERROR",
                        message=str(error),
                    )
                }
            except QueryTimeoutError as error:
                raise QueryTimedOut() from error
            except QuerySafetyError as error:
                raise UnsafeExecutionBlocked() from error

            self._log(
                request_id,
                "sql_executed",
                row_count=result.row_count,
                truncated=result.truncated,
                elapsed_ms=round(result.elapsed_ms, 3),
            )
            return {"query_result": result}

        def route_after_execution(
            state: SqlAgentState,
        ) -> Literal["rewrite_sql", "synthesize"]:
            return "synthesize" if "query_result" in state else "rewrite_sql"

        async def rewrite_sql(state: SqlAgentState) -> dict[str, object]:
            if state["rewrite_count"] >= self.settings.max_rewrites:
                error_id = state["request_id"][-8:]
                raise QueryGenerationFailed(f"查询改写次数耗尽：{error_id}")

            failed_sql = [*state["failed_sql"], state["sql"]]
            rewritten = await self.gateway.rewrite_sql(
                question=state["question"],
                focused_schema=state["focused_schema"],
                sql=state["sql"],
                error=state["validation"].message,
                history=failed_sql,
                request_id=state["request_id"],
            )
            if rewritten.sql.strip() in {
                item.strip() for item in failed_sql
            }:
                error_id = state["request_id"][-8:]
                raise QueryGenerationFailed(f"模型重复了失败 SQL：{error_id}")

            rewrite_count = state["rewrite_count"] + 1
            self._log(
                request_id,
                "sql_rewritten",
                rewrite_count=rewrite_count,
                sql=rewritten.sql,
            )
            return {
                "sql": rewritten.sql,
                "rewrite_count": rewrite_count,
                "failed_sql": failed_sql,
            }

        def refuse(_: SqlAgentState) -> dict[str, object]:
            self._log(request_id, "request_refused")
            return {
                "final_answer": (
                    "该请求涉及创建、修改、删除或导出数据；"
                    "系统仅允许只读查询，已拒绝执行。"
                )
            }

        async def synthesize(state: SqlAgentState) -> dict[str, object]:
            final = await self.gateway.synthesize(
                state["question"],
                state["sql"],
                state["query_result"],
                state["request_id"],
            )
            self._log(request_id, "answer_synthesized")
            return {"final_answer": final.answer}

        workflow.add_node("parse_database", parse_database)
        workflow.add_node("select_tables", select_tables)
        workflow.add_node("generate_sql", generate_sql)
        workflow.add_node("validate_sql", validate_sql)
        workflow.add_node("execute_sql", execute_sql)
        workflow.add_node("rewrite_sql", rewrite_sql)
        workflow.add_node("refuse", refuse)
        workflow.add_node("synthesize", synthesize)

        workflow.add_edge(START, "parse_database")
        workflow.add_edge("parse_database", "select_tables")
        workflow.add_edge("select_tables", "generate_sql")
        workflow.add_edge("generate_sql", "validate_sql")
        workflow.add_conditional_edges(
            "validate_sql", route_after_validation
        )
        workflow.add_conditional_edges(
            "execute_sql", route_after_execution
        )
        workflow.add_edge("rewrite_sql", "validate_sql")
        workflow.add_edge("refuse", END)
        workflow.add_edge("synthesize", END)

        graph = workflow.compile()
        result = await graph.ainvoke(
            {
                "request_id": request_id,
                "question": question,
                "rewrite_count": 0,
                "failed_sql": [],
            },
            config={
                "run_name": "sql_agent_workflow",
                "tags": ["sql-agent-lc"],
                "metadata": {
                    "request_id": request_id,
                    "provider": self.settings.provider,
                    "model": self.settings.model,
                },
            },
        )
        self._log(request_id, "request_completed")
        return FinalAnswer(answer=result["final_answer"])

    @staticmethod
    def _normalize_selection(
        selection: TableSelection, available: dict[str, object]
    ) -> list[str]:
        """去重并拒绝目录外表名；任一非法项都会使本次选择整体失败。"""
        result: list[str] = []
        for table in selection.tables:
            if table not in available:
                return []
            if table not in result:
                result.append(table)
        return result if 1 <= len(result) <= 6 else []

    @staticmethod
    def _log(request_id: str, event: str, **details: object) -> None:
        logger.info(
            json.dumps(
                {"request_id": request_id, "event": event, **details},
                ensure_ascii=False,
                default=str,
            )
        )