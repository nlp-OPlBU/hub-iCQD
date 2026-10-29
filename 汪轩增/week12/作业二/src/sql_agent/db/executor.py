"""只读 SQL 执行器：超时、行数和单元格长度限制。"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from ..config import settings
from ..models import ErrorCode, ExecutionResult


class SQLExecutionError(RuntimeError):
    def __init__(self, message: str, code: ErrorCode) -> None:
        super().__init__(message)
        self.code = code


def _readonly_uri(db_path: Path) -> str:
    return f"{Path(db_path).resolve().as_uri()}?mode=ro"


def _to_text(value: object, max_chars: int) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        text = value.hex()
    else:
        text = str(value)
    return text if len(text) <= max_chars else text[:max_chars] + "..."


class SQLExecutor:
    def __init__(
        self,
        db_path: Path,
        *,
        timeout_seconds: float | None = None,
        max_rows: int | None = None,
        max_cell_chars: int | None = None,
    ) -> None:
        self.db_path = Path(db_path).resolve()
        self.timeout_seconds = (
            settings.query_timeout_seconds if timeout_seconds is None else timeout_seconds
        )
        self.max_rows = settings.max_result_rows if max_rows is None else max_rows
        self.max_cell_chars = (
            settings.max_cell_chars if max_cell_chars is None else max_cell_chars
        )

    def execute(self, sql: str) -> ExecutionResult:
        conn = sqlite3.connect(_readonly_uri(self.db_path), uri=True)
        deadline = time.monotonic() + self.timeout_seconds

        def progress_handler() -> int:
            return 1 if time.monotonic() > deadline else 0

        try:
            conn.execute("PRAGMA query_only = ON")
            conn.set_progress_handler(progress_handler, 1000)
            cursor = conn.execute(sql)
            columns = [item[0] for item in (cursor.description or [])]
            fetched = cursor.fetchmany(self.max_rows + 1)
            truncated = len(fetched) > self.max_rows
            fetched = fetched[: self.max_rows]
            rows = [
                [_to_text(value, self.max_cell_chars) for value in row]
                for row in fetched
            ]
            return ExecutionResult(
                columns=columns,
                rows=rows,
                row_count=len(rows),
                truncated=truncated,
            )
        except sqlite3.OperationalError as exc:
            message = str(exc)
            code = (
                ErrorCode.TIMEOUT
                if "interrupted" in message.lower()
                else ErrorCode.RUNTIME
            )
            raise SQLExecutionError(message, code) from exc
        except sqlite3.Error as exc:
            raise SQLExecutionError(str(exc), ErrorCode.RUNTIME) from exc
        finally:
            conn.close()
