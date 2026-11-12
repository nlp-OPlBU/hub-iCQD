"""条件路由函数：读状态、返回下一个节点名。

纯函数、无副作用，便于单元测试。预算判断（check_budget）内嵌在
after_validate / after_execute 里，避免额外引入一个「预算检查」节点。
"""

from __future__ import annotations

from .state import GraphState


def after_select(state: GraphState) -> str:
    """选表后：需要澄清（或没选到表）走 clarify，否则进入生成。"""

    selection = state.get("selection")
    if selection is None or selection.needs_clarification or not selection.tables:
        return "clarify"
    return "generate_sql"


def after_validate(state: GraphState) -> str:
    """校验后：通过则执行，否则按预算决定改写或失败。"""

    validation = state.get("validation")
    if validation is not None and validation.ok:
        return "execute"
    return check_budget(state)


def after_execute(state: GraphState) -> str:
    """执行后：有结果则汇总，运行错误则按预算决定改写或失败。"""

    if state.get("execution") is not None:
        return "summarize"
    return check_budget(state)


def after_rewrite(state: GraphState) -> str:
    """改写后：无法修复直接失败，否则回到校验。"""

    return "fail" if state.get("cannot_fix") else "validate"


def check_budget(state: GraphState) -> str:
    """共享修复预算：达到上限返回 fail，否则返回 rewrite。"""

    max_attempts = state.get("max_repair_attempts", 0)
    if state.get("repair_attempts", 0) >= max_attempts:
        return "fail"
    return "rewrite"
