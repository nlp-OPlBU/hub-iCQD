"""文档状态机：表驱动，合法迁移集中一处，任何写入都必须经过校验。"""

from __future__ import annotations

from app.core.exceptions import ConflictStateError
from app.domain.enums import DocStatus

ALLOWED: dict[DocStatus, set[DocStatus]] = {
    DocStatus.PENDING: {DocStatus.PARSING, DocStatus.FAILED},
    # 中间态回 pending 是"崩溃恢复"通道：进程被杀后消息重投，必须能从头再来
    DocStatus.PARSING: {DocStatus.CHUNKING, DocStatus.FAILED, DocStatus.PENDING},
    DocStatus.CHUNKING: {DocStatus.INDEXING, DocStatus.FAILED, DocStatus.PENDING},
    DocStatus.INDEXING: {DocStatus.DONE, DocStatus.FAILED, DocStatus.PENDING},
    DocStatus.DONE: set(),
    DocStatus.FAILED: {DocStatus.PENDING},
}

# 状态推进时默认的进度百分比，写 Redis 投影时直接用
PROGRESS: dict[DocStatus, int] = {
    DocStatus.PENDING: 0,
    DocStatus.PARSING: 10,
    DocStatus.CHUNKING: 55,
    DocStatus.INDEXING: 80,
    DocStatus.DONE: 100,
    DocStatus.FAILED: 100,
}


def parse_status(raw: str | DocStatus) -> DocStatus:
    if isinstance(raw, DocStatus):
        return raw
    try:
        return DocStatus(raw)
    except ValueError as exc:
        raise ConflictStateError(f"未知文档状态: {raw}") from exc


def can_transition(current: str | DocStatus, target: str | DocStatus) -> bool:
    return parse_status(target) in ALLOWED[parse_status(current)]


def ensure_transition(current: str | DocStatus, target: str | DocStatus) -> None:
    current_enum, target_enum = parse_status(current), parse_status(target)
    if target_enum not in ALLOWED[current_enum]:
        raise ConflictStateError(
            f"非法状态迁移: {current_enum.value} -> {target_enum.value}",
            details={"from": current_enum.value, "to": target_enum.value},
        )


def max_attempts_reached(attempts: int, limit: int = 3) -> bool:
    return attempts >= limit
