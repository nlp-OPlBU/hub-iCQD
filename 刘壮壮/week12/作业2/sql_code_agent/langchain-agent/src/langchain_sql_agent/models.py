from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# 以下模型既是工作流阶段间的契约，也是 Agents SDK 的结构化输出类型。
class ColumnInfo(BaseModel):
    name: str
    declared_type: str
    not_null: bool
    primary_key: bool


class ForeignKeyInfo(BaseModel):
    from_column: str
    target_table: str
    target_column: str


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[ForeignKeyInfo]
    row_count: int
    samples: list[dict[str, Any]]


class SchemaCatalog(BaseModel):
    tables: dict[str, TableInfo]


class TableSelection(BaseModel):
    tables: list[str] = Field(description="The tables to be selected")


class SqlDraft(BaseModel):
    sql: str = Field(description="The SQL draft to be validated")


class ValidationResult(BaseModel):
    is_valid: bool
    category: Literal[
        "OK",
        "SAFETY_VIOLATION",
        "SYNTAX_ERROR",
        "SCHEMA_ERROR",
        "MULTI_STATEMENT",
    ]
    message: str
    read_tables: list[str] = Field(default_factory=list)


class QueryResult(BaseModel):
    columns: list[str]
    rows: list[list[Any | None]]
    row_count: int
    truncated: bool
    elapsed_ms: float


class FinalAnswer(BaseModel):
    answer: str = Field(description="The final answer to the question")
