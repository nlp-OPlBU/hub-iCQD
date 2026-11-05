"""LangGraph 版编排：编译图、执行、把图终态映射为 WorkflowOutput。

对外暴露与 ``SQLAgentWorkflow`` 相同的 ``run(question) -> WorkflowOutput``，
因此 CLI / 调用方可以无缝切换后端。
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator

from ..config import settings
from ..db.catalog import SchemaCatalog, get_catalog
from ..db.executor import SQLExecutor
from ..db.validator import SQLValidator
from ..models import WorkflowOutput
from .builder import build_graph
from .state import GraphState

logger = logging.getLogger(__name__)


def _preview(text: object, limit: int = 500) -> str:
    compact = " ".join(str(text).split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


# 节点名 -> 面向用户的中文阶段文案（前端流式进度用）。
_STAGE_LABELS = {
    "select_tables": "正在分析问题并选择数据表…",
    "generate_sql": "正在生成 SQL…",
    "validate": "正在校验 SQL…",
    "execute": "正在执行查询…",
    "rewrite": "正在修复 SQL…",
    "summarize": "正在整理结果…",
    "clarify": "需要澄清问题",
    "fail": "未能生成可执行 SQL",
}


class LangGraphWorkflow:
    """LangGraph 实现的 SQL Agent 工作流，接口对齐 SQLAgentWorkflow。"""

    def __init__(
        self,
        *,
        catalog: SchemaCatalog | None = None,
        validator: SQLValidator | None = None,
        executor: SQLExecutor | None = None,
        max_repair_attempts: int | None = None,
        checkpointer=None,
    ) -> None:
        self.catalog = catalog or get_catalog()
        self.validator = validator or SQLValidator(self.catalog.db_path)
        self.executor = executor or SQLExecutor(self.catalog.db_path)
        self.max_repair_attempts = (
            settings.max_repair_attempts
            if max_repair_attempts is None
            else max_repair_attempts
        )
        self.graph = build_graph(
            catalog=self.catalog,
            validator=self.validator,
            executor=self.executor,
            checkpointer=checkpointer,
        )

    async def run(self, question: str) -> WorkflowOutput:
        if not isinstance(question, str):
            raise TypeError("question 必须是字符串")
        question = question.strip()
        if not question:
            return WorkflowOutput(question=question, answer="请输入要查询的问题。")

        started = time.perf_counter()
        logger.info("workflow(langgraph) started question=%s", _preview(question, 300))
        try:
            state = await self.graph.ainvoke(
                {
                    "question": question,
                    "max_repair_attempts": self.max_repair_attempts,
                }
            )
            output = self._to_output(question, state)
            logger.info(
                "workflow(langgraph) finished elapsed=%.2fs tables=%s rows=%s "
                "repairs=%s outcome=%s error=%s",
                time.perf_counter() - started,
                output.tables,
                output.row_count,
                output.repair_attempts,
                state.get("outcome"),
                output.error,
            )
            return output
        except Exception as exc:  # noqa: BLE001 - CLI 需要稳定输出
            logger.exception("SQL agent (langgraph) workflow failed")
            return WorkflowOutput(
                question=question,
                answer=f"处理失败：{exc}",
                error=str(exc),
            )

    async def astream(self, question: str) -> AsyncIterator[tuple[str, dict]]:
        """逐节点流式执行，yield ``(事件类型, 事件体)``。

        事件类型：``stage``（每节点完成后）、``result``（最终 WorkflowOutput）、
        ``error``（顶层异常）。前端据此渲染进度条与最终答案。
        """

        if not isinstance(question, str):
            raise TypeError("question 必须是字符串")
        question = question.strip()
        if not question:
            output = WorkflowOutput(question=question, answer="请输入要查询的问题。")
            yield ("result", output.model_dump())
            return

        initial = {"question": question, "max_repair_attempts": self.max_repair_attempts}
        # 单独维护一份浅合并状态，只为最终映射 WorkflowOutput（其字段均为
        # last-write-wins），LangGraph 内部的 reducer 状态不受影响。
        acc = dict(initial)
        try:
            async for update in self.graph.astream(initial, stream_mode="updates"):
                for node_name, node_update in update.items():
                    acc.update(node_update)
                    yield ("stage", self._stage_event(node_name, node_update))
            yield ("result", self._to_output(question, acc).model_dump())
        except Exception as exc:  # noqa: BLE001 - 流式也要稳定收尾
            logger.exception("SQL agent (langgraph) stream failed")
            yield ("error", {"message": str(exc)})

    def _stage_event(self, node_name: str, node_update: dict) -> dict:
        event = {"stage": node_name, "label": _STAGE_LABELS.get(node_name, node_name)}
        if node_name in ("generate_sql", "rewrite") and node_update.get("sql"):
            event["sql"] = node_update["sql"]
        if node_name == "execute":
            execution = node_update.get("execution")
            if execution is not None:
                event["row_count"] = execution.row_count
            else:
                validation = node_update.get("validation")
                if validation is not None:
                    event["error"] = validation.message
        return event

    def _to_output(self, question: str, state: GraphState) -> WorkflowOutput:
        selection = state.get("selection")
        execution = state.get("execution")
        answer = state.get("answer")
        return WorkflowOutput(
            question=question,
            answer=answer.answer if answer else "处理失败。",
            sql=state.get("sql"),
            tables=(selection.tables if selection else []),
            row_count=(execution.row_count if execution else 0),
            truncated=(execution.truncated if execution else False),
            repair_attempts=state.get("repair_attempts", 0),
            evidence=(answer.evidence if answer else []),
            caveats=(answer.caveats if answer else []),
            error=state.get("error"),
        )
