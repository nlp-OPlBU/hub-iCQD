"""状态机：合法链路、非法迁移、未知状态。"""

from __future__ import annotations

import pytest

from app.core.exceptions import ConflictStateError
from app.domain.enums import DocStatus
from app.domain.state_machine import can_transition, ensure_transition, max_attempts_reached


def test_happy_path_is_allowed() -> None:
    path = [
        DocStatus.PENDING,
        DocStatus.PARSING,
        DocStatus.CHUNKING,
        DocStatus.INDEXING,
        DocStatus.DONE,
    ]
    for current, target in zip(path, path[1:]):
        assert can_transition(current, target), f"{current} -> {target} 应被允许"


@pytest.mark.parametrize(
    "current,target",
    [
        (DocStatus.DONE, DocStatus.PENDING),
        (DocStatus.PENDING, DocStatus.DONE),
        (DocStatus.PARSING, DocStatus.INDEXING),
        (DocStatus.DONE, DocStatus.FAILED),
    ],
)
def test_illegal_transition_raises(current: DocStatus, target: DocStatus) -> None:
    assert not can_transition(current, target)
    with pytest.raises(ConflictStateError) as exc:
        ensure_transition(current, target)
    assert exc.value.code.value == "CONFLICT_STATE"
    assert exc.value.http_status == 409


def test_failed_can_retry() -> None:
    assert can_transition(DocStatus.FAILED, DocStatus.PENDING)
    assert can_transition("pending", "failed")
    assert not can_transition("failed", "done")


def test_unknown_status_rejected() -> None:
    with pytest.raises(ConflictStateError):
        ensure_transition("ghost", "pending")


def test_attempt_limit() -> None:
    assert not max_attempts_reached(2)
    assert max_attempts_reached(3)
