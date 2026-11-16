"""Task processor boundary shared by the worker and its tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from app.repositories.task import TaskClaim


@dataclass(frozen=True)
class TaskResult:
    artifact_key: str
    index_name: str
    indexed_at: datetime
    retrieval_mode: str


class TaskProcessingError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True,
                 details: dict | None = None):
        super().__init__(message)
        self.code, self.message = code, message
        self.retryable, self.details = retryable, details or {}


class TaskProcessor(Protocol):
    def process(self, claim: TaskClaim) -> TaskResult: ...
