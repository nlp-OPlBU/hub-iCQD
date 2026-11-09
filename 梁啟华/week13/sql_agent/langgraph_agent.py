"""基于 LangGraph StateGraph 的 SQL Agent。

在原 ``SQLAgent``（线性工作流）之上，用一张状态图显式表达各阶段与循环：

    START -> load_schema -> select_tables -> generate_sql -> validate_sql
    validate_sql  --校验通过--> execute_sql --执行成功--> summarize -> END
    validate_sql  --校验失败--> rewrite_sql -> validate_sql（受 max_rewrite_rounds 限制）
    execute_sql   --执行失败--> rewrite_sql
    改写次数用尽 -> fail -> END

对外保留原有 ``SQLAgent`` 的构造参数、``ask()`` 返回结构（``AgentResult``）
以及 ``close()`` 等接口，便于直接替换使用。节点内部复用原 ``SQLAgent``
的 table 选择、SQL 生成/校验/改写、结果汇总能力，不重复实现业务逻辑。
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from sql_agent import (
    AgentResult,
    SQLAgent,
    SQLAgentError,
    TraceStep,
    _brief_error,
)

logger = logging.getLogger(__name__)


def _merge_trace(
    existing: list[TraceStep], update: list[TraceStep]
) -> list[TraceStep]:
    """状态中 trace 字段的 reducer：把节点新增的记录追加到已有列表。"""
    return existing + update


class AgentState(TypedDict, total=False):
    """图状态定义。

    字段:
        question: 用户问题。
        tables: 选中的表。
        sql: 当前 SQL。
        attempts: 当前 SQL 的生成次数（初始生成算 1 次，每次改写 +1）。
        valid: 最近一次校验是否通过。
        issues: 最近一次校验/执行失败的原因。
        exec_result: SQL 执行结果（columns/rows/row_count/truncated）。
        answer: 最终自然语言回答。
        error: 工作流终止时的错误信息，非空表示失败。
        trace: 各阶段执行记录，通过 reducer 追加。
    """

    question: str
    tables: list[str]
    sql: str
    attempts: int
    valid: bool
    issues: str
    exec_result: dict[str, Any] | None
    answer: str
    error: str | None
    trace: Annotated[list[TraceStep], _merge_trace]


class LangGraphSQLAgent(SQLAgent):
    """把 ``SQLAgent`` 的各阶段编排为 LangGraph 状态图。"""

    def __init__(
        self,
        db_path: str | None = None,
        llm: Any | None = None,
        max_rewrite_rounds: int = 2,
        max_rows: int = 200,
        semantic_check: bool = True,
    ) -> None:
        # 复用原有初始化：读取 schema、创建 LLM 客户端、数据库连接等
        super().__init__(
            db_path=db_path,
            llm=llm,
            max_rewrite_rounds=max_rewrite_rounds,
            max_rows=max_rows,
            semantic_check=semantic_check,
        )
        self.graph = self._build_graph()

    # --------------------------------------------------------------- 图构建
    def _build_graph(self):
        builder = StateGraph(AgentState)

        builder.add_node("load_schema", self._node_load_schema)
        builder.add_node("select_tables", self._node_select_tables)
        builder.add_node("generate_sql", self._node_generate_sql)
        builder.add_node("validate_sql", self._node_validate_sql)
        builder.add_node("execute_sql", self._node_execute_sql)
        builder.add_node("rewrite_sql", self._node_rewrite_sql)
        builder.add_node("summarize", self._node_summarize)
        builder.add_node("fail", self._node_fail)

        builder.add_edge(START, "load_schema")
        builder.add_edge("load_schema", "select_tables")
        builder.add_edge("select_tables", "generate_sql")
        builder.add_edge("generate_sql", "validate_sql")

        # 校验通过 -> 执行；校验失败 -> 改写（或重试次数用尽后失败）
        builder.add_conditional_edges(
            "validate_sql",
            self._route_after_validate,
            {"execute": "execute_sql", "rewrite": "rewrite_sql", "fail": "fail"},
        )
        # 执行成功 -> 汇总；执行失败 -> 改写（或失败）
        builder.add_conditional_edges(
            "execute_sql",
            self._route_after_execute,
            {"summarize": "summarize", "rewrite": "rewrite_sql", "fail": "fail"},
        )
        # 改写完成后回到校验，形成循环
        builder.add_edge("rewrite_sql", "validate_sql")

        builder.add_edge("summarize", END)
        builder.add_edge("fail", END)

        return builder.compile()

    # ------------------------------------------------------------------ 节点
    def _node_load_schema(self, state: AgentState) -> dict[str, Any]:
        detail = (
            f"共 {len(self.parser.table_names)} 张表、"
            f"{len(self.parser.foreign_keys)} 条外键关系"
        )
        return {
            "trace": [
                TraceStep(
                    "数据库解析", detail, {"tables": self.parser.table_names}
                )
            ]
        }

    def _node_select_tables(self, state: AgentState) -> dict[str, Any]:
        question = state["question"]
        tables = self.select_tables(question)
        return {
            "tables": tables,
            "trace": [
                TraceStep("table 选择", f"选中 {', '.join(tables)}", {"tables": tables})
            ],
        }

    def _node_generate_sql(self, state: AgentState) -> dict[str, Any]:
        sql = self.generate_sql(state["question"], state["tables"])
        return {
            "sql": sql,
            "attempts": 1,
            "valid": False,
            "issues": "",
            "exec_result": None,
            "trace": [TraceStep("SQL 生成", sql, {"sql": sql})],
        }

    def _node_validate_sql(self, state: AgentState) -> dict[str, Any]:
        valid, issues = self.validate_sql(
            state["sql"], state["question"], state["tables"]
        )
        return {
            "valid": valid,
            "issues": "" if valid else issues,
            "trace": [
                TraceStep(
                    "SQL 校验",
                    "通过" if valid else f"未通过：{issues}",
                    {"valid": valid, "issues": issues, "sql": state["sql"]},
                )
            ],
        }

    def _node_execute_sql(self, state: AgentState) -> dict[str, Any]:
        try:
            result = self.parser.execute_sql(state["sql"], self.max_rows)
        except Exception as exc:  # noqa: BLE001 - 执行失败交给改写节点
            issues = f"SQL 执行失败：{_brief_error(exc)}"
            logger.warning(issues)
            return {
                "exec_result": None,
                "issues": issues,
                "trace": [
                    TraceStep("SQL 执行", issues, {"error": issues, "sql": state["sql"]})
                ],
            }

        detail = f"返回 {result['row_count']} 行" + (
            "（已截断）" if result["truncated"] else ""
        )
        return {
            "exec_result": result,
            "issues": "",
            "trace": [
                TraceStep(
                    "SQL 执行",
                    detail,
                    {
                        "columns": result["columns"],
                        "row_count": result["row_count"],
                        "truncated": result["truncated"],
                    },
                )
            ],
        }

    def _node_rewrite_sql(self, state: AgentState) -> dict[str, Any]:
        round_index = state["attempts"] - 1
        new_sql = self.rewrite_sql(
            state["question"],
            state["tables"],
            state["sql"],
            state.get("issues") or "未知问题",
        )
        return {
            "sql": new_sql,
            "attempts": state["attempts"] + 1,
            "valid": False,
            "trace": [
                TraceStep(
                    "SQL 改写",
                    f"第 {round_index + 1} 次改写完成",
                    {"sql": new_sql, "issues": state.get("issues", "")},
                )
            ],
        }

    def _node_summarize(self, state: AgentState) -> dict[str, Any]:
        exec_result = state["exec_result"]
        answer = self.summarize(state["question"], state["sql"], exec_result)
        return {
            "answer": answer,
            "trace": [TraceStep("结果汇总", answer, {"answer": answer})],
        }

    def _node_fail(self, state: AgentState) -> dict[str, Any]:
        attempts = state.get("attempts", 1)
        issues = state.get("issues") or "未知原因"
        return {
            "error": f"SQL 经过 {attempts} 次生成仍未通过校验：{issues}",
        }

    # -------------------------------------------------------------- 条件路由
    def _can_rewrite(self, state: AgentState) -> bool:
        """是否还有改写额度：初始生成占 1 次，最多 max_rewrite_rounds 次改写。"""
        return state.get("attempts", 1) - 1 < self.max_rewrite_rounds

    def _route_after_validate(self, state: AgentState) -> str:
        if state.get("valid"):
            return "execute"
        return "rewrite" if self._can_rewrite(state) else "fail"

    def _route_after_execute(self, state: AgentState) -> str:
        if state.get("exec_result") is not None:
            return "summarize"
        return "rewrite" if self._can_rewrite(state) else "fail"

    # ----------------------------------------------------------- 工作流入口
    def ask(self, question: str) -> AgentResult:
        """执行状态图，返回与 ``SQLAgent.ask`` 相同结构的结果。"""
        question = (question or "").strip()
        if not question:
            raise ValueError("问题不能为空")

        self._trace = []
        initial_state: AgentState = {
            "question": question,
            "tables": [],
            "sql": "",
            "attempts": 0,
            "valid": False,
            "issues": "",
            "exec_result": None,
            "answer": "",
            "error": None,
            "trace": [],
        }
        # 每次校验/执行/改写最多 3 个节点，预留足够步数避免 recursion_limit 误报
        recursion_limit = max(25, 3 * self.max_rewrite_rounds + 10)
        final_state = self.graph.invoke(
            initial_state, config={"recursion_limit": recursion_limit}
        )

        self._trace = list(final_state.get("trace", []))
        if final_state.get("error"):
            raise SQLAgentError(final_state["error"])

        exec_result = final_state.get("exec_result")
        if exec_result is None:
            raise SQLAgentError("SQL 执行结果为空")

        return AgentResult(
            question=question,
            tables=list(final_state.get("tables", [])),
            sql=final_state.get("sql", ""),
            attempts=final_state.get("attempts", 1),
            columns=exec_result["columns"],
            rows=exec_result["rows"],
            row_count=exec_result["row_count"],
            truncated=exec_result["truncated"],
            answer=final_state.get("answer", ""),
            trace=self._trace,
        )
