"""SQL 安全门禁与语法/对象校验：只编译、不执行。

LLM 不能"批准"自己生成的 SQL，是否可进入执行阶段由本模块决定。
"""

from __future__ import annotations

import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from .catalog import make_readonly_url
from .models import ErrorCode, ValidationResult

FORBIDDEN_KEYWORDS = {
    "insert",
    "update",
    "delete",
    "replace",
    "create",
    "drop",
    "alter",
    "pragma",
    "attach",
    "detach",
    "vacuum",
    "reindex",
    "trigger",
    "begin",
    "commit",
    "rollback",
    "savepoint",
    "release",
}


def normalize_sql(raw_sql: str) -> str:
    """去掉模型常见的 Markdown 代码围栏和尾部分号。"""

    sql = (raw_sql or "").strip()
    if sql.startswith("```"):
        lines = sql.splitlines()
        if lines and lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        sql = "\n".join(lines).strip()

    while sql.endswith(";"):
        sql = sql[:-1].rstrip()
    return sql


def _mask_literals_and_comments(sql: str) -> str:
    """把字符串、标识符引用和注释替换为空格，避免关键字误判。"""

    chars = list(sql)
    i = 0
    length = len(sql)

    def blank(start: int, end: int) -> None:
        for index in range(start, min(end, length)):
            if chars[index] != "\n":
                chars[index] = " "

    while i < length:
        if sql.startswith("--", i):
            end = sql.find("\n", i)
            end = length if end == -1 else end
            blank(i, end)
            i = end
        elif sql.startswith("/*", i):
            end = sql.find("*/", i + 2)
            end = length if end == -1 else end + 2
            blank(i, end)
            i = end
        elif sql[i] in {"'", '"', "`"}:
            quote = sql[i]
            j = i + 1
            while j < length:
                if sql[j] == quote:
                    if j + 1 < length and sql[j + 1] == quote:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            blank(i, j)
            i = j
        elif sql[i] == "[":
            end = sql.find("]", i + 1)
            end = length if end == -1 else end + 1
            blank(i, end)
            i = end
        else:
            i += 1

    return "".join(chars)


class SQLValidator:
    """校验顺序：安全门禁（单语句 / SELECT|WITH / 危险关键字）→ EXPLAIN 编译。"""

    def __init__(self, db_path: Path, engine: Engine | None = None) -> None:
        self.engine = engine or create_engine(make_readonly_url(Path(db_path)))

    def validate(self, raw_sql: str) -> ValidationResult:
        sql = normalize_sql(raw_sql)
        if not sql:
            return ValidationResult(
                ok=False,
                code=ErrorCode.UNSAFE,
                message="SQL 为空",
                normalized_sql=sql,
            )

        masked = _mask_literals_and_comments(sql)
        if ";" in masked.strip():
            return ValidationResult(
                ok=False,
                code=ErrorCode.UNSAFE,
                message="只允许单条 SQL 语句",
                normalized_sql=sql,
            )

        if not re.match(r"^\s*(select|with)\b", masked, re.IGNORECASE):
            return ValidationResult(
                ok=False,
                code=ErrorCode.UNSAFE,
                message="只允许 SELECT 或 WITH ... SELECT 查询",
                normalized_sql=sql,
            )

        for keyword in FORBIDDEN_KEYWORDS:
            if re.search(rf"\b{keyword}\b", masked, re.IGNORECASE):
                return ValidationResult(
                    ok=False,
                    code=ErrorCode.UNSAFE,
                    message=f"禁止使用 {keyword.upper()} 等写操作或管理语句",
                    normalized_sql=sql,
                )

        try:
            with self.engine.connect() as conn:
                # EXPLAIN 只编译 SQL，不产生查询结果。
                conn.execute(text(f"EXPLAIN {sql}"))
        except Exception as exc:
            message = str(exc)
            lowered = message.lower()
            if "ambiguous column" in lowered:
                code = ErrorCode.AMBIGUOUS
            elif "no such table" in lowered or "no such column" in lowered:
                code = ErrorCode.UNKNOWN_OBJECT
            else:
                code = ErrorCode.SYNTAX
            return ValidationResult(
                ok=False,
                code=code,
                message=message.splitlines()[0][:2000],
                normalized_sql=sql,
            )

        return ValidationResult(ok=True, normalized_sql=sql)
