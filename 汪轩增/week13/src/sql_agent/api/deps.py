"""后端工作流实例：懒加载单例，避免每个请求重建 catalog（解析约 0.8s）。"""

from __future__ import annotations

import os

from ..graph.workflow import LangGraphWorkflow
from ..workflow import SQLAgentWorkflow


def backend_name() -> str:
    """当前编排后端：langgraph（默认，支持流式）或 code。"""

    return os.getenv("SQL_AGENT_BACKEND", "langgraph")


_workflow: LangGraphWorkflow | SQLAgentWorkflow | None = None


def get_workflow():
    global _workflow
    if _workflow is None:
        if backend_name() == "langgraph":
            _workflow = LangGraphWorkflow()
        else:
            _workflow = SQLAgentWorkflow()
    return _workflow
