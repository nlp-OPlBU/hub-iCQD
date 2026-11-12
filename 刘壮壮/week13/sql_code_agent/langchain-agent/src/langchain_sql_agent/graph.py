import uuid
import logging
import json
from collections.abc import Callable
from time import perf_counter
from typing import Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from .chains import LangChainModelGateway, ModelGateway
from .config import Settings
from .models import (
    AgentRunResult,
    AnswerDelta,
    ConversationTurn,
    FinalAnswer,
    ProgressDetail,
    ProgressUpdate,
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
    conversation_id: str
    question: str
    standalone_question: str
    history: list[ConversationTurn]
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
    refused: bool
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

    async def answer(
        self,
        question: str,
        *,
        history: list[ConversationTurn] | None = None,
    ) -> FinalAnswer:
        result = await self.answer_detailed(question, history=history)
        return FinalAnswer(answer=result.answer)

    async def answer_detailed(
        self,
        question: str,
        *,
        request_id: str | None = None,
        conversation_id: str | None = None,
        history: list[ConversationTurn] | None = None,
        on_event: Callable[[ProgressUpdate | AnswerDelta], None] | None = None,
    ) -> AgentRunResult:
        question = question.strip()
        if not question:
            raise WorkflowError("问题不能为空")
        request_id = request_id or self.new_request_id()
        conversation_id = conversation_id or self.new_conversation_id()
        history = list(history or [])[-5:]
        self._log(request_id, "request_started")

        def progress(
            stage: Literal[
                "contextualize",
                "parse_database",
                "select_tables",
                "generate_sql",
                "validate_sql",
                "rewrite_sql",
                "execute_sql",
                "synthesize",
            ],
            status: Literal["running", "completed"],
            message: str,
            *,
            description: str | None = None,
            details: list[ProgressDetail] | None = None,
            duration_ms: float | None = None,
            tone: Literal["info", "success", "warning", "danger"] = "info",
        ) -> None:
            if on_event is not None:
                on_event(
                    ProgressUpdate(
                        stage=stage,
                        status=status,
                        message=message,
                        description=description,
                        details=details or [],
                        duration_ms=duration_ms,
                        tone=tone,
                    )
                )

        workflow = StateGraph(SqlAgentState)

        async def contextualize(state: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("contextualize", "running", "正在理解对话上下文")
            if state["history"]:
                resolved = await self.gateway.contextualize(
                    state["question"],
                    state["history"],
                    state["request_id"],
                )
                standalone_question = resolved.question.strip()
                if not standalone_question:
                    raise WorkflowError("模型返回了空问题")
            else:
                standalone_question = state["question"]
            self._log(
                request_id,
                "question_contextualized",
                history_count=len(state["history"]),
            )
            history_count = len(state["history"])
            progress(
                "contextualize",
                "completed",
                "对话上下文已理解",
                description=(
                    "已结合最近的成功对话，将当前追问整理为可独立理解的问题。"
                    if history_count
                    else "当前问题可以独立理解，无需引用历史对话。"
                ),
                details=[
                    ProgressDetail(
                        label="上下文来源",
                        value="当前问题 + 对话历史" if history_count else "仅当前问题",
                        format="status",
                    ),
                    ProgressDetail(label="参考历史", value=f"{history_count} 轮"),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="success",
            )
            return {"standalone_question": standalone_question}

        def parse_database(_: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("parse_database", "running", "正在读取数据库结构")
            catalog = self.schema_service.load()
            self._log(
                request_id, "database_parsed", table_count=len(catalog.tables)
            )
            progress(
                "parse_database",
                "completed",
                "数据库结构读取完成",
                description="已读取可查询的数据表、字段、外键和行数，用于后续精确选表。",
                details=[
                    ProgressDetail(
                        label="数据库",
                        value=self.settings.db_path.name,
                        format="code",
                    ),
                    ProgressDetail(
                        label="可用数据表", value=f"{len(catalog.tables)} 张"
                    ),
                    ProgressDetail(
                        label="结构范围", value="字段、外键、行数", format="status"
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="success",
            )
            return {
                "catalog": catalog,
                "selection_context": self.schema_service.selection_context(catalog),
            }

        async def select_tables(state: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("select_tables", "running", "正在选择相关数据表")
            selection_context = state["selection_context"]
            catalog = state["catalog"]

            for attempt in range(2):
                candidate = await self.gateway.select_tables(
                    state["standalone_question"],
                    selection_context,
                    state["request_id"],
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
                    progress(
                        "select_tables",
                        "completed",
                        "相关数据表已确定",
                        description="已根据问题语义从数据库目录中定位最相关的数据表。",
                        details=[
                            ProgressDetail(
                                label="选中数据表",
                                value="、".join(normalized),
                                format="code",
                            ),
                            ProgressDetail(
                                label="选择次数", value=f"{attempt + 1} 次"
                            ),
                            ProgressDetail(
                                label="选择结果",
                                value=f"{len(normalized)} 张有效表",
                                format="status",
                            ),
                        ],
                        duration_ms=(perf_counter() - started) * 1000,
                        tone="success" if attempt == 0 else "warning",
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
            started = perf_counter()
            progress("generate_sql", "running", "正在生成查询")
            draft = await self.gateway.generate_sql(
                state["standalone_question"],
                state["focused_schema"],
                state["request_id"],
            )
            self._log(request_id, "sql_generated", sql=draft.sql)
            progress(
                "generate_sql",
                "completed",
                "候选 SQL 已生成",
                description="已根据问题和选中表的结构生成候选查询，下一步将进行确定性安全校验。",
                details=[
                    ProgressDetail(
                        label="基于数据表",
                        value="、".join(state["selected_tables"]),
                        format="code",
                    ),
                    ProgressDetail(
                        label="当前状态", value="等待安全校验", format="status"
                    ),
                    ProgressDetail(
                        label="完整语句", value="请在“SQL”标签页查看"
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="success",
            )
            return {"sql": draft.sql}

        def validate_sql(state: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("validate_sql", "running", "正在执行安全校验")
            validation = self.validator.validate(
                state["sql"], state["standalone_question"]
            )
            self._log(
                request_id,
                "sql_validated",
                category=validation.category,
                read_tables=validation.read_tables,
            )
            category_labels = {
                "OK": "校验通过",
                "SAFETY_VIOLATION": "已拦截危险操作",
                "MULTI_STATEMENT": "已拦截多条语句",
                "SYNTAX_ERROR": "发现语法问题",
                "SCHEMA_ERROR": "发现结构引用问题",
            }
            blocked = validation.category in {
                "SAFETY_VIOLATION",
                "MULTI_STATEMENT",
            }
            progress(
                "validate_sql",
                "completed",
                category_labels[validation.category],
                description=(
                    "候选 SQL 已通过单语句、只读权限和数据库编译预检。"
                    if validation.is_valid
                    else (
                        "检测到非只读或多语句请求，流程已在执行数据库前安全终止。"
                        if blocked
                        else "候选 SQL 未通过预检，将在重试预算内进行修复并重新校验。"
                    )
                ),
                details=[
                    ProgressDetail(
                        label="校验结论",
                        value=category_labels[validation.category],
                        format="status",
                    ),
                    ProgressDetail(
                        label="校验类别",
                        value=validation.category,
                        format="code",
                    ),
                    ProgressDetail(
                        label="读取数据表",
                        value=(
                            "、".join(validation.read_tables)
                            if validation.read_tables
                            else "未进入读取"
                        ),
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone=(
                    "success"
                    if validation.is_valid
                    else ("danger" if blocked else "warning")
                ),
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
            started = perf_counter()
            progress("execute_sql", "running", "正在执行只读查询")
            try:
                result = self.executor.execute(state["sql"])
            except RepairableQueryError as error:
                progress(
                    "execute_sql",
                    "completed",
                    "执行阶段发现可修复问题",
                    description="只读执行器未返回结果，流程将修复 SQL 后重新进行安全校验。",
                    details=[
                        ProgressDetail(
                            label="处理方式", value="进入 SQL 修复", format="status"
                        ),
                        ProgressDetail(
                            label="安全状态", value="未产生数据写入"
                        ),
                    ],
                    duration_ms=(perf_counter() - started) * 1000,
                    tone="warning",
                )
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
            progress(
                "execute_sql",
                "completed",
                "只读查询执行完成",
                description="查询已在独立只读连接和资源限制下完成。",
                details=[
                    ProgressDetail(
                        label="返回结果", value=f"{result.row_count} 行"
                    ),
                    ProgressDetail(
                        label="数据库耗时", value=f"{result.elapsed_ms:.1f} ms"
                    ),
                    ProgressDetail(
                        label="结果状态",
                        value="已截断" if result.truncated else "完整返回",
                        format="status",
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="warning" if result.truncated else "success",
            )
            return {"query_result": result}

        def route_after_execution(
            state: SqlAgentState,
        ) -> Literal["rewrite_sql", "synthesize"]:
            return "synthesize" if "query_result" in state else "rewrite_sql"

        async def rewrite_sql(state: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("rewrite_sql", "running", "正在修复查询")
            if state["rewrite_count"] >= self.settings.max_rewrites:
                error_id = state["request_id"][-8:]
                raise QueryGenerationFailed(f"查询改写次数耗尽：{error_id}")

            failed_sql = [*state["failed_sql"], state["sql"]]
            rewritten = await self.gateway.rewrite_sql(
                question=state["standalone_question"],
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
            progress(
                "rewrite_sql",
                "completed",
                "候选 SQL 已修复",
                description="已根据预检错误和表结构修复查询；修复结果不会直接执行，必须重新校验。",
                details=[
                    ProgressDetail(
                        label="触发原因",
                        value=state["validation"].category,
                        format="code",
                    ),
                    ProgressDetail(
                        label="修复次数",
                        value=f"{rewrite_count} / {self.settings.max_rewrites}",
                    ),
                    ProgressDetail(
                        label="下一步骤", value="重新执行安全校验", format="status"
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="warning",
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
                ),
                "refused": True,
            }

        async def synthesize(state: SqlAgentState) -> dict[str, object]:
            started = perf_counter()
            progress("synthesize", "running", "正在生成回答")
            chunks: list[str] = []
            async for chunk in self.gateway.stream_synthesize(
                state["standalone_question"],
                state["sql"],
                state["query_result"],
                state["request_id"],
            ):
                chunks.append(chunk)
                if on_event is not None:
                    on_event(AnswerDelta(delta=chunk))
            answer = "".join(chunks).strip()
            if not answer:
                raise WorkflowError("模型返回了空答案")
            self._log(request_id, "answer_synthesized")
            query_result = state["query_result"]
            progress(
                "synthesize",
                "completed",
                "回答生成完成",
                description="已严格基于查询结果整理自然语言回答，没有引入数据库之外的事实。",
                details=[
                    ProgressDetail(
                        label="事实来源",
                        value=f"{query_result.row_count} 行查询结果",
                    ),
                    ProgressDetail(
                        label="结果范围",
                        value="截断结果" if query_result.truncated else "完整结果",
                        format="status",
                    ),
                    ProgressDetail(
                        label="输出方式", value="流式生成"
                    ),
                ],
                duration_ms=(perf_counter() - started) * 1000,
                tone="warning" if query_result.truncated else "success",
            )
            return {"final_answer": answer}

        workflow.add_node("contextualize", contextualize)
        workflow.add_node("parse_database", parse_database)
        workflow.add_node("select_tables", select_tables)
        workflow.add_node("generate_sql", generate_sql)
        workflow.add_node("validate_sql", validate_sql)
        workflow.add_node("execute_sql", execute_sql)
        workflow.add_node("rewrite_sql", rewrite_sql)
        workflow.add_node("refuse", refuse)
        workflow.add_node("synthesize", synthesize)

        workflow.add_edge(START, "contextualize")
        workflow.add_edge("contextualize", "parse_database")
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
                "conversation_id": conversation_id,
                "question": question,
                "history": history,
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
        refused = result.get("refused", False)
        return AgentRunResult(
            request_id=request_id,
            conversation_id=conversation_id,
            answer=result["final_answer"],
            selected_tables=result.get("selected_tables", []),
            sql=None if refused else result.get("sql"),
            query_result=result.get("query_result"),
            refused=refused,
        )

    @staticmethod
    def new_request_id() -> str:
        return f"trace_{uuid.uuid4().hex}"

    @staticmethod
    def new_conversation_id() -> str:
        return f"conv_{uuid.uuid4().hex}"

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