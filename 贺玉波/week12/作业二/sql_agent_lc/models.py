"""数据契约：所有 LLM 链路都用 Pydantic 结构化输出，避免解析自由文本。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TableSelection(BaseModel):
    """选表链路的输出。"""

    tables: list[str] = Field(description="回答该问题所需的最少表名集合")
    reason: str = Field(description="选择这些表的理由")
    confidence: float = Field(default=0.0, ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None


class SQLDraft(BaseModel):
    """SQL 生成 / 改写链路的输出。"""

    sql: str = Field(description="单条 SQLite SELECT 或 WITH ... SELECT 语句")
    explanation: str = Field(default="", description="SQL 如何回答用户问题")
    assumptions: list[str] = Field(default_factory=list)
    cannot_fix: bool = Field(default=False, description="改写时是否无法修复")


class ErrorCode(str, Enum):
    UNSAFE = "unsafe"
    SYNTAX = "syntax"
    UNKNOWN_OBJECT = "unknown_object"
    AMBIGUOUS = "ambiguous"
    RUNTIME = "runtime"
    TIMEOUT = "timeout"
    EMPTY = "empty"


class ValidationResult(BaseModel):
    ok: bool
    code: ErrorCode | None = None
    message: str = ""
    normalized_sql: str = ""


class ExecutionResult(BaseModel):
    columns: list[str] = Field(default_factory=list)
    rows: list[list[str]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False


class SQLAttempt(BaseModel):
    sql: str
    source: str
    code: ErrorCode | None = None
    error: str = ""


class FinalAnswer(BaseModel):
    """结果汇总链路的输出。"""

    answer: str = Field(description="面向用户的自然语言答案")
    evidence: list[str] = Field(default_factory=list, description="支撑答案的具体数值")
    caveats: list[str] = Field(default_factory=list)


class QueryState(BaseModel):
    """一次查询的完整状态，由 Orchestrator 维护。"""

    question: str
    selection: TableSelection | None = None
    schema_context: str = ""
    sql: str | None = None
    attempts: list[SQLAttempt] = Field(default_factory=list)
    validation: ValidationResult | None = None
    execution: ExecutionResult | None = None
    answer: FinalAnswer | None = None
    repair_attempts: int = 0
    stage: str = "init"


class WorkflowOutput(BaseModel):
    """面向用户的最终 JSON，由代码组装，不由模型拼接。"""

    question: str
    answer: str
    sql: str | None = None
    tables: list[str] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    repair_attempts: int = 0
    evidence: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    error: str | None = None
