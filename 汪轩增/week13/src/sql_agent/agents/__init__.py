"""四个 LLM Agent 的模块级定义（LangChain 结构化输出链）。"""

from __future__ import annotations

from .result_summarizer import result_summary_agent
from .sql_generator import sql_generator_agent
from .sql_rewriter import sql_rewriter_agent
from .table_selector import table_selector_agent

# 编排层按名称取用，便于日志与测试统一遍历。
AGENTS = (
    table_selector_agent,
    sql_generator_agent,
    sql_rewriter_agent,
    result_summary_agent,
)

__all__ = [
    "AGENTS",
    "result_summary_agent",
    "sql_generator_agent",
    "sql_rewriter_agent",
    "table_selector_agent",
]
