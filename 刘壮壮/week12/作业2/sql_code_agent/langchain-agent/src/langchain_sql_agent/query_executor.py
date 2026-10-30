from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Protocol

from .models import QueryResult
from .schema_service import readonly_uri
from .sql_safety import make_readonly_authorizer, normalize_sql


class QueryExecutionError(RuntimeError):
    pass


class RepairableQueryError(QueryExecutionError):
    pass


class QueryTimeoutError(QueryExecutionError):
    pass


class QuerySafetyError(QueryExecutionError):
    pass

class QueryExecutor(Protocol):
    def execute(self, sql: str) -> QueryResult: ...

class ReadOnlyQueryExecutor:
    """在独立只读连接中执行已校验 SQL，并施加资源上限。"""

    def __init__(
        self,
        db_path: Path,
        max_result_rows: int = 100,
        max_progress_steps: int = 1_000_000,
    ) -> None:
        self.db_path = db_path
        self.max_result_rows = max_result_rows
        self.max_progress_steps = max_progress_steps

    def execute(self, sql: str) -> QueryResult:
        sql = normalize_sql(sql)
        started = time.perf_counter()
        connection = sqlite3.connect(readonly_uri(self.db_path), uri=True)
        try:
            # 执行阶段重复设置只读边界，不能假设预检结果天然可信。
            connection.execute("PRAGMA query_only = ON")
            connection.set_authorizer(make_readonly_authorizer())
            progress_calls = 0
            interval = 1000
            max_calls = max(1, self.max_progress_steps // interval)

            def progress_handler() -> int:
                """返回非零值会中断可能失控的笛卡尔积或递归查询。"""
                nonlocal progress_calls
                progress_calls += 1
                return int(progress_calls > max_calls)

            connection.set_progress_handler(progress_handler, interval)
            cursor = connection.execute(sql)
            columns = [
                description[0] for description in (cursor.description or ())
            ]
            # 多取一行仅用于判断是否截断，不把该行返回给总结 Agent。
            rows = cursor.fetchmany(self.max_result_rows + 1)
            truncated = len(rows) > self.max_result_rows
            retained = rows[: self.max_result_rows]
            elapsed_ms = (time.perf_counter() - started) * 1000
            return QueryResult(
                columns=columns,
                rows=[list(row) for row in retained],
                row_count=len(retained),
                truncated=truncated,
                elapsed_ms=elapsed_ms,
            )
        except sqlite3.OperationalError as error:
            lowered = str(error).lower()
            if "interrupted" in lowered:
                raise QueryTimeoutError("查询超过资源限制") from error
            if "not authorized" in lowered or "readonly" in lowered:
                raise QuerySafetyError("执行器拒绝了非只读操作") from error
            raise RepairableQueryError(str(error)) from error
        except sqlite3.DatabaseError as error:
            raise QuerySafetyError("数据库拒绝执行该查询") from error
        finally:
            connection.close()
