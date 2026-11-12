"""LangGraph 图状态定义（TypedDict + reducer）。

与 ``models.QueryState`` 的区别：这是给 LangGraph 用的 channel 声明，
``attempts`` 用 ``operator.add`` 做追加 reducer，等价于现有 workflow 里的
``state.attempts.append(...)``。其余字段沿用 ``models.py`` 里的 Pydantic 契约，
不在图上重复定义业务模型。
"""

from __future__ import annotations

from operator import add
from typing import Annotated

from typing_extensions import TypedDict

from ..models import (
    ExecutionResult,
    FinalAnswer,
    SQLAttempt,
    TableSelection,
    ValidationResult,
)


class GraphState(TypedDict, total=False):
    """一次查询在图上的完整状态。字段即 LangGraph 的 state channel。"""

    # 输入与运行参数
    question: str
    max_repair_attempts: int

    # 选表结果
    selection: TableSelection | None
    schema_context: str

    # SQL 与修复历史（attempts 使用追加 reducer）
    sql: str | None
    attempts: Annotated[list[SQLAttempt], add]
    repair_attempts: int

    # 校验与执行结果
    validation: ValidationResult | None
    execution: ExecutionResult | None

    # 改写无法修复标记（由 rewrite 节点设置）
    cannot_fix: bool
    cannot_fix_reason: str

    # 终态
    answer: FinalAnswer | None
    outcome: str
    error: str | None
