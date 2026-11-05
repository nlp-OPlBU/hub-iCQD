"""组装 StateGraph 并编译。"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph

from ..db.catalog import SchemaCatalog, get_catalog
from ..db.executor import SQLExecutor
from ..db.validator import SQLValidator
from .nodes import make_nodes
from .routes import after_execute, after_rewrite, after_select, after_validate
from .state import GraphState


def build_graph(
    *,
    catalog: SchemaCatalog | None = None,
    validator: SQLValidator | None = None,
    executor: SQLExecutor | None = None,
    checkpointer=None,
):
    """构造编译后的 LangGraph 图。

    ``checkpointer`` 可选（如 ``MemorySaver()``），用于断点续跑/多轮澄清；
    默认不开启，此时状态以内存中的 Python 对象透传，Pydantic 模型无需序列化。
    """

    catalog = catalog or get_catalog()
    validator = validator or SQLValidator(catalog.db_path)
    executor = executor or SQLExecutor(catalog.db_path)
    nodes = make_nodes(catalog=catalog, validator=validator, executor=executor)

    graph = StateGraph(GraphState)
    graph.add_node("select_tables", nodes["select_tables"])
    graph.add_node("generate_sql", nodes["generate_sql"])
    graph.add_node("validate", nodes["validate"])
    graph.add_node("execute", nodes["execute"])
    graph.add_node("rewrite", nodes["rewrite"])
    graph.add_node("summarize", nodes["summarize"])
    graph.add_node("clarify", nodes["clarify"])
    graph.add_node("fail", nodes["fail"])

    graph.add_edge(START, "select_tables")
    graph.add_conditional_edges(
        "select_tables",
        after_select,
        {"clarify": "clarify", "generate_sql": "generate_sql"},
    )
    graph.add_edge("generate_sql", "validate")
    graph.add_conditional_edges(
        "validate",
        after_validate,
        {"execute": "execute", "rewrite": "rewrite", "fail": "fail"},
    )
    graph.add_conditional_edges(
        "execute",
        after_execute,
        {"summarize": "summarize", "rewrite": "rewrite", "fail": "fail"},
    )
    graph.add_conditional_edges(
        "rewrite",
        after_rewrite,
        {"validate": "validate", "fail": "fail"},
    )
    graph.add_edge("summarize", END)
    graph.add_edge("clarify", END)
    graph.add_edge("fail", END)

    return graph.compile(checkpointer=checkpointer)
