from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from .models import ValidationResult
from .schema_service import readonly_uri


DANGEROUS_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "REPLACE",
    "ATTACH",
    "DETACH",
    "VACUUM",
    "REINDEX",
    "ANALYZE",
    "PRAGMA",
)

UNSAFE_QUESTION_PATTERNS = (
    r"删除|删掉|清空|移除",
    r"修改|更新|改成|设为",
    r"新建|创建|建一张|复制.*(?:表|进去)|导出",
    r"\b(?:delete|drop|truncate|update|insert|create|alter|attach|vacuum)\b",
)

DENIED_AUTHORIZER_ACTIONS = {
    # authorizer 在 SQLite 编译语句时触发，是正则检查之外的安全边界。
    value
    for name in (
        "SQLITE_INSERT",
        "SQLITE_UPDATE",
        "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX",
        "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX",
        "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER",
        "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER",
        "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_INDEX",
        "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX",
        "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER",
        "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER",
        "SQLITE_DROP_VIEW",
        "SQLITE_ALTER_TABLE",
        "SQLITE_REINDEX",
        "SQLITE_ANALYZE",
        "SQLITE_ATTACH",
        "SQLITE_DETACH",
        "SQLITE_TRANSACTION",
        "SQLITE_SAVEPOINT",
        "SQLITE_PRAGMA",
    )
    if (value := getattr(sqlite3, name, None)) is not None
}


def normalize_sql(sql: str) -> str:
    """清理模型偶尔附带的单层 Markdown SQL 代码围栏。"""
    value = sql.strip()
    match = re.fullmatch(r"```(?:sql)?\s*(.*?)\s*```", value, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else value


def lexical_mask(sql: str) -> str:
    """遮蔽字符串、引用标识符和注释，同时保留分号及关键字的位置。

    后续关键字扫描只检查真正的 SQL token，避免把字符串中的
    ``'DELETE'`` 或注释中的危险词误判为写操作。
    """
    chars = list(sql)
    index = 0
    state = "normal"
    while index < len(chars):
        current = chars[index]
        next_char = chars[index + 1] if index + 1 < len(chars) else ""
        if state == "normal":
            if current == "'":
                chars[index] = " "
                state = "single"
            elif current == '"':
                chars[index] = " "
                state = "double"
            elif current == "[":
                chars[index] = " "
                state = "bracket"
            elif current == "-" and next_char == "-":
                chars[index] = chars[index + 1] = " "
                index += 1
                state = "line_comment"
            elif current == "/" and next_char == "*":
                chars[index] = chars[index + 1] = " "
                index += 1
                state = "block_comment"
        elif state == "single":
            chars[index] = " "
            if current == "'" and next_char == "'":
                chars[index + 1] = " "
                index += 1
            elif current == "'":
                state = "normal"
        elif state == "double":
            chars[index] = " "
            if current == '"' and next_char == '"':
                chars[index + 1] = " "
                index += 1
            elif current == '"':
                state = "normal"
        elif state == "bracket":
            chars[index] = " "
            if current == "]":
                state = "normal"
        elif state == "line_comment":
            chars[index] = " "
            if current in "\r\n":
                state = "normal"
        elif state == "block_comment":
            chars[index] = " "
            if current == "*" and next_char == "/":
                chars[index + 1] = " "
                index += 1
                state = "normal"
        index += 1
    return "".join(chars)


def make_readonly_authorizer(read_tables: set[str] | None = None):
    """创建供预检和执行连接共用的 SQLite authorizer。"""

    def authorizer(
        action: int,
        arg1: str | None,
        arg2: str | None,
        database_name: str | None,
        trigger_name: str | None,
    ) -> int:
        del database_name, trigger_name
        if action in DENIED_AUTHORIZER_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_READ and arg1 and read_tables is not None:
            read_tables.add(arg1)
        if (
            action == sqlite3.SQLITE_FUNCTION
            and (arg2 or arg1)
            and (arg2 or arg1 or "").lower() == "load_extension"
        ):
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    return authorizer


class SqlSafetyValidator:
    """以静态规则、SQLite 编译预检和 authorizer 分层校验 SQL。"""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def validate(self, sql: str, question: str | None = None) -> ValidationResult:
        normalized = normalize_sql(sql)
        # 即使模型把危险请求改写成 SELECT，也不能绕过原始用户意图检查。
        if question and any(
            re.search(pattern, question, re.IGNORECASE)
            for pattern in UNSAFE_QUESTION_PATTERNS
        ):
            return self._failure(
                "SAFETY_VIOLATION", "请求涉及创建、修改、删除或导出数据"
            )
        if not normalized:
            return self._failure("SYNTAX_ERROR", "SQL 为空")

        masked = lexical_mask(normalized)
        first_keyword = re.search(r"\b([A-Za-z]+)\b", masked)
        if not first_keyword or first_keyword.group(1).upper() not in {"SELECT", "WITH"}:
            return self._failure(
                "SAFETY_VIOLATION", "只允许 SELECT 或只读 WITH 查询"
            )

        semicolons = [index for index, char in enumerate(masked) if char == ";"]
        # 允许唯一的句末分号，以及字符串内部已被遮蔽的分号。
        if len(semicolons) > 1 or (
            semicolons and masked[semicolons[0] + 1 :].strip()
        ):
            return self._failure("MULTI_STATEMENT", "只允许执行一条 SQL 语句")

        keyword_pattern = r"\b(?:" + "|".join(DANGEROUS_KEYWORDS) + r")\b"
        if re.search(keyword_pattern, masked, re.IGNORECASE):
            return self._failure(
                "SAFETY_VIOLATION", "SQL 包含不允许的写入或管理操作"
            )

        read_tables: set[str] = set()
        try:
            connection = sqlite3.connect(readonly_uri(self.db_path), uri=True)
            try:
                connection.execute("PRAGMA query_only = ON")
                connection.set_authorizer(make_readonly_authorizer(read_tables))
                # EXPLAIN 会让 SQLite 解析并解析名称，但不会运行用户查询。
                connection.execute(f"EXPLAIN QUERY PLAN {normalized}").fetchall()
            finally:
                connection.close()
        except sqlite3.DatabaseError as error:
            message = str(error)
            lowered = message.lower()
            if "not authorized" in lowered or "readonly" in lowered:
                return self._failure("SAFETY_VIOLATION", "SQLite 拒绝了非只读操作")
            category = (
                "SCHEMA_ERROR"
                if "no such table" in lowered
                or "no such column" in lowered
                or "ambiguous column" in lowered
                else "SYNTAX_ERROR"
            )
            return self._failure(category, message)

        return ValidationResult(
            is_valid=True,
            category="OK",
            message="SQL 校验通过",
            read_tables=sorted(read_tables),
        )

    @staticmethod
    def _failure(category: str, message: str) -> ValidationResult:
        return ValidationResult(
            is_valid=False, category=category, message=message, read_tables=[]
        )
