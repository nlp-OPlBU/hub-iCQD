"""SQL Agent 的 Pydantic 数据契约。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field



class TableSelection(BaseModel):
    """表选择 Agent 的结构化结果。"""

    tables: list[str] = Field(description="回答该问题所需的最少表名集合")
    reason: str = Field(description="选择这些表的理由")
    confidence: float = Field(default=0.0, ge=0, le=1)
    needs_clarification: bool = False
    clarification_question: str | None = None


class SQLDraft(BaseModel):
    """SQL 生成 / 改写 Agent 的结构化结果。"""

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
    """结果汇总 Agent 的结构化结果。"""

    answer: str = Field(description="面向用户的自然语言答案")
    evidence: list[str] = Field(default_factory=list, description="支撑答案的具体数值")
    caveats: list[str] = Field(default_factory=list)


class WorkflowOutput(BaseModel):
    """面向用户的最终 JSON 结构，由代码组装。"""

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


class QueryState(BaseModel):
    """一次查询在 LangGraph 节点之间传递的完整状态。

    节点返回字典，只更新其中出现的字段，其他字段沿用上一节点的值。
    当前没有为字段设置累加 reducer，因此列表更新也会整体替换；
    追加 SQL 历史时需要返回包含旧记录的新列表，不能原地修改输入状态。
    """

    # 用户的原始查询意图；经 run() 去除首尾空白后，始终传给各个 Agent。
    question: str
    # 最近一次选表结果：表名、选择理由、置信度，以及是否需要向用户澄清。
    selection: TableSelection | None = None
    # 已完成的选表调用次数，包含首次调用；最多两次，与 SQL 修复次数独立。
    selection_attempts: int = 0
    # 代码检查选表后发现的问题，如表不存在或外键不连通；供下一次选表纠正。
    # 无问题或模型主动请求澄清时为空字符串。
    selection_feedback: str = ""
    # 已选表的字段、统计、样例与关联关系文本，供 SQL 生成和修复共同使用。
    schema_context: str = ""
    # 当前待校验的 SQL；生成前为 None，执行成功后替换为实际执行的规范化 SQL。
    sql: str | None = None
    # 按顺序保存初次生成及已接受的改写；每条含 SQL、来源和实际失败信息。
    # default_factory 为每次查询创建独立列表，避免请求之间共享历史。
    attempts: list[SQLAttempt] = Field(default_factory=list)
    # 最近一次安全/编译校验结果；执行失败也转换成此结构，统一传给修复节点。
    # 接受新 SQL 后清空，直到新 SQL 再次经过校验。
    validation: ValidationResult | None = None
    # 成功执行后保留的列名、结果行、行数和截断标记；未执行或执行失败时为 None。
    execution: ExecutionResult | None = None
    # 汇总节点产出的答案、证据和限制说明，也可以是空结果答复或降级表格。
    # 此处尚未附加 SQL、表名等面向调用方的完整输出字段。
    answer: FinalAnswer | None = None
    # 已接受并进入后续校验的 SQL 改写数量；不包含首次生成和 cannot_fix 的调用。
    repair_attempts: int = 0
    # 改写 Agent 返回 cannot_fix 时的用户答复；非 None 时让改写路由直接结束。
    # 底层错误仍保存在 validation 中，此字段不表示所有类型的失败。
    failure_answer: str | None = None
    # finalize 节点组装的最终响应；run() 从图结果中取出它，CLI 再序列化为 JSON。
    output: WorkflowOutput | None = None
    # 最近完成的处理阶段，供查看状态和流式调试；条件路由不依赖这个字符串。
    stage: str = "init"
