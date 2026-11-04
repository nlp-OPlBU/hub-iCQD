"""四个 LLM Agent 的模块级定义（参考 ref/agent-demo.py 的写法）。"""

from __future__ import annotations

import os


from ..config import settings


# os.environ.setdefault("OPENAI_BASE_URL", settings.base_url)

# DashScope / 多数兼容端点不支持 Responses API，统一走 Chat Completions。
# set_default_openai_api("chat_completions")

from .result_summarizer import result_summary_agent  # noqa: E402
from .sql_generator import sql_generator_agent  # noqa: E402
from .sql_rewriter import sql_rewriter_agent  # noqa: E402
from .table_selector import table_selector_agent  # noqa: E402

__all__ = [
    "result_summary_agent",
    "sql_generator_agent",
    "sql_rewriter_agent",
    "table_selector_agent",
]
