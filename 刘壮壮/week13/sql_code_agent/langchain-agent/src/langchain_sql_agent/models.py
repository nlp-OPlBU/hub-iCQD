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


class ConversationTurn(BaseModel):
    """供下一轮指代消解使用的精简历史，不包含完整查询结果。"""

    question: str = Field(min_length=1, max_length=2_000)
    answer: str = Field(min_length=1, max_length=8_000)
    sql: str | None = Field(default=None, max_length=20_000)


class StandaloneQuestion(BaseModel):
    question: str = Field(
        min_length=1,
        max_length=2_000,
        description="结合有限对话历史后可独立理解的问题",
    )


WorkflowStage = Literal[
    "contextualize",
    "parse_database",
    "select_tables",
    "generate_sql",
    "validate_sql",
    "rewrite_sql",
    "execute_sql",
    "synthesize",
]


class ProgressUpdate(BaseModel):
    """面向 UI 的稳定阶段进度，不暴露 LangGraph 内部状态。"""

    stage: WorkflowStage
    status: Literal["running", "completed"]
    message: str
    description: str | None = None
    details: list["ProgressDetail"] = Field(default_factory=list)
    duration_ms: float | None = Field(default=None, ge=0)
    tone: Literal["info", "success", "warning", "danger"] = "info"


class ProgressDetail(BaseModel):
    """单条用户可见的阶段事实，值已经过脱敏并适合直接展示。"""

    label: str
    value: str
    format: Literal["text", "status", "code"] = "text"


class AnswerDelta(BaseModel):
    """最终自然语言答案的单个增量文本块。"""

    delta: str


class AgentRunResult(BaseModel):
    """一次完整问答的富结果，供 API 和详情抽屉使用。"""

    request_id: str
    conversation_id: str
    answer: str
    selected_tables: list[str] = Field(default_factory=list)
    sql: str | None = None
    query_result: QueryResult | None = None
    refused: bool = False
