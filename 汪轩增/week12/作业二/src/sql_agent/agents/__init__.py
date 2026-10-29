"""四个 LLM Agent 的模块级定义（langchain 版）。

每个 Agent 模块导出一个 ``run(prompt)`` 协程，对应 openai-agents 里
``Runner.run(agent, prompt)`` 的调用。编排仍由 workflow.py 的确定性代码完成。
"""

from __future__ import annotations

from .result_summarizer import run as summarize
from .sql_generator import run as generate_sql
from .sql_rewriter import run as rewrite_sql
from .table_selector import run as select_tables

__all__ = ["generate_sql", "rewrite_sql", "select_tables", "summarize"]
