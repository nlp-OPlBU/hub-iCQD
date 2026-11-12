"""SQL Agent REST 接口（FastAPI）。

在 ``LangGraphSQLAgent`` / ``SQLAgent`` 的基础上暴露 HTTP 接口：

- ``GET  /health``        健康检查
- ``GET  /schema``        完整 schema（可按表过滤）
- ``GET  /schema/tables`` 表清单与字段统计
- ``POST /query``         自然语言问答
- ``POST /sql/validate``  单条 SQL 校验（只读/单语句/语法）
- ``POST /sql/execute``   单条 SQL 执行

启动方式（在 sql_agent 目录下）：

    uvicorn api:app --host 0.0.0.0 --port 8000

配置环境变量：
    OPENAI_API_KEY / OPENAI_BASE_URL / SQL_AGENT_MODEL  大模型配置（同 llm.py）
    SQL_AGENT_DB_PATH    SQLite 路径，默认沿用 sql_agent.DEFAULT_DB_PATH
    SQL_AGENT_ENGINE     graph（默认）或 classic

测试可通过 ``create_app(agent_factory=...)`` 注入假 LLM 和临时数据库。
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from contextlib import asynccontextmanager
from typing import Any, Callable

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from db_parser import normalize_sql
from sql_agent import DEFAULT_DB_PATH, SQLAgent, SQLAgentError

logger = logging.getLogger(__name__)

AgentFactory = Callable[[], SQLAgent]


# ---------------------------------------------------------------- Pydantic 模型
class FieldInfo(BaseModel):
    """字段统计信息。"""

    name: str
    type: str
    primary_key: bool = False
    nullable: bool = True
    distinct: int | None = None
    nan_count: int | None = None
    min: Any = None
    max: Any = None
    mode: Any = None


class TableInfo(BaseModel):
    """单表信息。"""

    name: str
    row_count: int
    fields: list[FieldInfo]


class TableListResponse(BaseModel):
    """表清单响应。"""

    count: int
    tables: list[TableInfo]


class SchemaResponse(BaseModel):
    """schema 文本响应。

    字段内部名为 ``schema_text``，对外 JSON 键仍为 ``schema``，
    避免与 Pydantic BaseModel 的 ``schema`` 属性冲突。
    """

    model_config = ConfigDict(populate_by_name=True)

    tables: list[str]
    schema_text: str = Field(
        ..., serialization_alias="schema", validation_alias="schema"
    )


class TraceStepResponse(BaseModel):
    """工作流阶段记录。"""

    stage: str
    detail: str
    payload: dict[str, Any] = Field(default_factory=dict)


class QueryRequest(BaseModel):
    """自然语言问答请求。"""

    question: str = Field(
        ...,
        min_length=1,
        max_length=2000,
        description="自然语言问题",
        examples=["每位客户的总消费是多少？"],
    )
    include_rows: bool = Field(
        default=True,
        description="是否在响应中返回完整数据行（false 时只返回行数）",
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class QueryResponse(BaseModel):
    """自然语言问答响应。"""

    question: str
    answer: str
    sql: str
    tables: list[str]
    attempts: int
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float
    trace: list[TraceStepResponse]


class SqlValidateRequest(BaseModel):
    """SQL 校验请求。"""

    sql: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="待校验的 SQLite 查询",
        examples=["SELECT * FROM invoices LIMIT 10"],
    )

    @field_validator("sql")
    @classmethod
    def _strip_sql(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SQL 不能为空")
        return value


class SqlValidateResponse(BaseModel):
    """SQL 校验响应。"""

    valid: bool
    message: str
    sql: str


class SqlExecuteRequest(BaseModel):
    """SQL 执行请求。"""

    sql: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="待执行的只读 SQLite 查询",
        examples=["SELECT * FROM invoices LIMIT 10"],
    )
    max_rows: int = Field(
        default=200, ge=1, le=5000, description="最多返回的数据行数"
    )

    @field_validator("sql")
    @classmethod
    def _strip_sql(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("SQL 不能为空")
        return value


class SqlExecuteResponse(BaseModel):
    """SQL 执行响应。"""

    sql: str
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float


class HealthResponse(BaseModel):
    """健康检查响应。"""

    status: str
    db_path: str | None = None
    engine: str | None = None
    table_count: int | None = None
    error: str | None = None


# -------------------------------------------------------------------- 默认工厂
def _env_flag(name: str, default: bool = True) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def default_agent_factory(db_path: str | None = None) -> AgentFactory:
    """按环境变量构造真实 Agent（默认 LangGraph 引擎）。"""

    target_db = db_path or os.getenv("SQL_AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
    engine = os.getenv("SQL_AGENT_ENGINE", "graph").strip().lower()
    max_rewrite_rounds = int(os.getenv("SQL_AGENT_MAX_REWRITES", "2"))
    max_rows = int(os.getenv("SQL_AGENT_MAX_ROWS", "200"))
    semantic_check = _env_flag("SQL_AGENT_SEMANTIC_CHECK", True)

    def factory() -> SQLAgent:
        kwargs = dict(
            db_path=target_db,
            max_rewrite_rounds=max_rewrite_rounds,
            max_rows=max_rows,
            semantic_check=semantic_check,
        )
        if engine == "classic":
            return SQLAgent(**kwargs)
        from langgraph_agent import LangGraphSQLAgent

        return LangGraphSQLAgent(**kwargs)

    return factory


# ------------------------------------------------------------------ 应用工厂
def create_app(
    agent_factory: AgentFactory | None = None,
    db_path: str | None = None,
) -> FastAPI:
    """创建 FastAPI 应用。

    参数:
        agent_factory: 可选的 Agent 工厂，测试时注入假 LLM / 临时数据库。
        db_path: 可选的数据库路径，透传给默认工厂。
    """

    factory = agent_factory or default_agent_factory(db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.agent = None
        app.state.agent_error = None
        try:
            app.state.agent = factory()
        except Exception as exc:  # noqa: BLE001 - 启动失败时降级为 503
            app.state.agent_error = str(exc)
            logger.error("SQL Agent 初始化失败：%s", exc)
        yield
        agent = app.state.agent
        if agent is not None:
            agent.close()

    api = FastAPI(
        title="SQL Agent API",
        version="1.0.0",
        description="基于 LangGraph / SQLite 的自然语言查询接口",
        lifespan=lifespan,
    )

    def require_agent(request: Request) -> SQLAgent:
        agent = getattr(request.app.state, "agent", None)
        if agent is None:
            reason = getattr(request.app.state, "agent_error", None) or "未初始化"
            raise HTTPException(status_code=503, detail=f"Agent 不可用：{reason}")
        return agent

    # ---------------------------------------------------------- 错误映射
    @api.exception_handler(SQLAgentError)
    async def _handle_sql_agent_error(
        request: Request, exc: SQLAgentError
    ) -> JSONResponse:
        # 校验失败/重试耗尽属于业务失败，返回 422
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "sql_agent_error"},
        )

    @api.exception_handler(ValueError)
    async def _handle_value_error(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "code": "invalid_request"},
        )

    @api.exception_handler(sqlite3.Error)
    async def _handle_sqlite_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content={"detail": f"数据库错误：{exc}", "code": "database_error"},
        )

    @api.exception_handler(RuntimeError)
    async def _handle_runtime_error(request: Request, exc: RuntimeError) -> JSONResponse:
        # 典型场景：大模型鉴权失败或调用失败
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc), "code": "llm_error"},
        )

    # -------------------------------------------------------------- 健康检查
    @api.get("/health", response_model=HealthResponse, tags=["system"])
    async def health(request: Request) -> Any:
        agent: SQLAgent | None = getattr(request.app.state, "agent", None)
        if agent is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "error": getattr(request.app.state, "agent_error", None)
                    or "Agent 未初始化",
                },
            )
        try:
            agent.parser.conn.execute("SELECT 1").fetchone()
        except sqlite3.Error as exc:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "db_path": agent.db_path,
                    "engine": type(agent).__name__,
                    "error": f"数据库不可用：{exc}",
                },
            )
        return HealthResponse(
            status="ok",
            db_path=agent.db_path,
            engine=type(agent).__name__,
            table_count=len(agent.parser.table_names),
        )

    # -------------------------------------------------------------- schema
    @api.get("/schema/tables", response_model=TableListResponse, tags=["schema"])
    async def list_tables(agent: SQLAgent = Depends(require_agent)) -> TableListResponse:
        tables = [
            TableInfo(
                name=name,
                row_count=agent.parser.get_row_count(name),
                fields=[
                    FieldInfo(**field)
                    for field in agent.parser.get_table_fields(name)
                ],
            )
            for name in agent.parser.table_names
        ]
        return TableListResponse(count=len(tables), tables=tables)

    @api.get("/schema", response_model=SchemaResponse, tags=["schema"])
    async def get_schema(
        tables: str | None = Query(
            default=None,
            description="逗号分隔的表名，例如 customers,invoices；不传返回全部",
        ),
        agent: SQLAgent = Depends(require_agent),
    ) -> SchemaResponse:
        names = (
            [item.strip() for item in tables.split(",") if item.strip()]
            if tables
            else list(agent.parser.table_names)
        )
        unknown = [name for name in names if name not in agent.parser.table_names]
        if unknown:
            raise HTTPException(status_code=404, detail=f"表不存在：{', '.join(unknown)}")
        return SchemaResponse(
            tables=names, schema_text=agent.parser.get_schema_text(names)
        )

    # -------------------------------------------------------------- 问答
    @api.post("/query", response_model=QueryResponse, tags=["agent"])
    async def query(
        payload: QueryRequest, agent: SQLAgent = Depends(require_agent)
    ) -> QueryResponse:
        started = time.perf_counter()
        result = agent.ask(payload.question)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return QueryResponse(
            question=result.question,
            answer=result.answer,
            sql=result.sql,
            tables=result.tables,
            attempts=result.attempts,
            columns=result.columns,
            rows=result.rows if payload.include_rows else [],
            row_count=result.row_count,
            truncated=result.truncated,
            elapsed_ms=elapsed_ms,
            trace=[
                TraceStepResponse(
                    stage=step.stage, detail=step.detail, payload=step.payload
                )
                for step in result.trace
            ],
        )

    # ---------------------------------------------------------- 单条 SQL 能力
    @api.post("/sql/validate", response_model=SqlValidateResponse, tags=["sql"])
    async def validate_sql(
        payload: SqlValidateRequest, agent: SQLAgent = Depends(require_agent)
    ) -> SqlValidateResponse:
        sql = normalize_sql(payload.sql)
        valid, message = agent.parser.check_sql(sql)
        return SqlValidateResponse(valid=valid, message=message, sql=sql)

    @api.post("/sql/execute", response_model=SqlExecuteResponse, tags=["sql"])
    async def execute_sql(
        payload: SqlExecuteRequest, agent: SQLAgent = Depends(require_agent)
    ) -> SqlExecuteResponse:
        sql = normalize_sql(payload.sql)
        valid, message = agent.parser.check_sql(sql)
        if not valid:
            # 非只读 / 多语句 / 语法错误：请求本身不合法
            raise HTTPException(status_code=400, detail=f"SQL 校验失败：{message}")

        started = time.perf_counter()
        result = agent.parser.execute_sql(sql, payload.max_rows)
        elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
        return SqlExecuteResponse(
            sql=sql,
            columns=result["columns"],
            rows=result["rows"],
            row_count=result["row_count"],
            truncated=result["truncated"],
            elapsed_ms=elapsed_ms,
        )

    return api


app = create_app()
