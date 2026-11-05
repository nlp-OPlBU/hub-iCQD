"""SQL Agent Web 前端后端（FastAPI）。

在原有 ``LangGraphSQLAgent`` / ``SQLAgent`` 之上增加：

- 会话级多轮上下文（同一 ``session_id`` 内保留最近几轮问答）
- 对话接口 ``POST /api/chat``
- 健康检查 ``GET /api/health``
- 会话重置 ``DELETE /api/session/{session_id}``
- 托管 ``frontend/`` 下的静态页面

原有 ``api.py`` 保持不变，本文件只做「导入 + 轻量包装」，不重写 Agent 逻辑。

启动：
    python web_app.py --port 8000
或：
    python -m uvicorn web_app:app --host 127.0.0.1 --port 8000

环境变量：
    OPENAI_API_KEY / OPENAI_BASE_URL / SQL_AGENT_MODEL  大模型配置（同 llm.py）
    SQL_AGENT_DB_PATH    SQLite 路径，默认 sql_agent.DEFAULT_DB_PATH
    SQL_AGENT_ENGINE     graph（默认）或 classic
    SQL_AGENT_MAX_REWRITES / SQL_AGENT_MAX_ROWS / SQL_AGENT_SEMANTIC_CHECK
    SQL_AGENT_TIMEOUT    单次对话超时秒数，默认 180
    SQL_AGENT_MOCK=1     离线模拟模式（不调用真实大模型，仅用于演示/联调）
    WEB_SESSION_TTL_MINUTES  会话空闲过期时间，默认 60
    WEB_MAX_SESSIONS         最多保留会话数，默认 200
    WEB_HISTORY_TURNS        参与上下文改写的最近轮数，默认 5
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sqlite3
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from sql_agent import DEFAULT_DB_PATH, SQLAgent, SQLAgentError, TraceStep

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,64}$")

CONTEXT_REWRITE_PROMPT = """你是一个对话查询助手。下面是用户与 SQL 查询助手的最近对话，请把「最新问题」改写成一条不依赖上下文、语义完整、可以直接生成 SQL 的独立问题。

对话历史：
{#history#}

最新问题：{#question#}

要求：
1. 补全指代和省略的信息（例如“它 / 这个 / 那 tracks 表呢”指代的对象）；
2. 保留原问题的查询意图，不要增加原问题没有的筛选条件；
3. 只输出改写后的问题，不要解释，不要输出 SQL，也不要加引号。
"""

FOLLOWUP_MARKERS = (
    "它",
    "他",
    "她",
    "这个",
    "这些",
    "那些",
    "该表",
    "上述",
    "刚才",
    "前面",
    "其中",
    "他们",
    "她们",
    "继续",
    "再来",
    "那",
)


# ---------------------------------------------------------------- 自定义异常
class AgentUnavailable(Exception):
    """Agent 未初始化或初始化失败。"""


class RequestTimeout(Exception):
    """单次对话超时。"""


# ------------------------------------------------------------------ 运行时
class AgentRuntime:
    """在单独线程中创建并使用 Agent。

    SQLite 连接默认只能在其创建线程中使用；这里用单线程执行器保证
    ``DBParser`` 的连接、查询、关闭都发生在同一个线程，同时允许事件循环
    通过 ``asyncio.wait_for`` 对同步 Agent 调用施加超时。
    """

    def __init__(self, factory: Callable[[], SQLAgent]) -> None:
        self._factory = factory
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="sql-agent"
        )
        self.agent: SQLAgent | None = None
        self.agent_error: str | None = None
        self.mock = False
        self.timeout = 180.0

    async def initialize(self) -> None:
        self.mock = _env_flag("SQL_AGENT_MOCK", False)
        self.timeout = float(os.getenv("SQL_AGENT_TIMEOUT", "180"))
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._create_agent), timeout=60
            )
        except asyncio.TimeoutError:
            self.agent_error = "Agent 初始化超时（超过 60 秒）"
            logger.error(self.agent_error)
        except Exception as exc:  # noqa: BLE001 - 启动失败降级为 503
            self.agent_error = str(exc)
            logger.error("Agent 初始化失败：%s", exc)

    def _create_agent(self) -> None:
        self.agent = self._factory()

    async def call(self, fn: Callable[[SQLAgent], Any]) -> Any:
        if self.agent is None:
            raise AgentUnavailable(self.agent_error or "Agent 未初始化")
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, lambda: fn(self.agent))
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=self.timeout)
        except asyncio.TimeoutError as exc:
            raise RequestTimeout(
                f"处理超时（超过 {self.timeout:g} 秒），请简化问题后重试。"
            ) from exc

    async def close(self) -> None:
        agent = self.agent
        if agent is not None:
            loop = asyncio.get_running_loop()
            try:
                await loop.run_in_executor(self._executor, agent.close)
            except Exception:  # noqa: BLE001 - 关闭失败不影响退出
                logger.warning("关闭 Agent 失败", exc_info=True)
        self._executor.shutdown(wait=False, cancel_futures=True)


# ------------------------------------------------------------------ 会话存储
@dataclass
class Turn:
    """一轮问答，只保留上下文改写所需的最小信息。"""

    question: str
    resolved: str
    answer: str
    sql: str


class SessionStore:
    """进程内会话存储（LRU + TTL）。"""

    def __init__(self, max_sessions: int, ttl_seconds: float, max_turns: int) -> None:
        self.max_sessions = max_sessions
        self.ttl_seconds = ttl_seconds
        self.max_turns = max_turns
        self._sessions: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def history(self, session_id: str) -> list[Turn]:
        self._prune()
        item = self._sessions.get(session_id)
        if item is None:
            return []
        item["updated"] = time.time()
        self._sessions.move_to_end(session_id)
        return list(item["turns"])

    def append(self, session_id: str, turn: Turn) -> None:
        self._prune()
        item = self._sessions.setdefault(
            session_id, {"turns": [], "updated": time.time()}
        )
        item["turns"].append(turn)
        if len(item["turns"]) > self.max_turns:
            item["turns"] = item["turns"][-self.max_turns :]
        item["updated"] = time.time()
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > self.max_sessions:
            self._sessions.popitem(last=False)

    def clear(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None

    def _prune(self) -> None:
        now = time.time()
        expired = [
            sid
            for sid, item in self._sessions.items()
            if now - item["updated"] > self.ttl_seconds
        ]
        for sid in expired:
            self._sessions.pop(sid, None)


# ------------------------------------------------------------------ 离线模拟
class MockLLM:
    """没有 API Key 时的离线模拟客户端。

    只替换 LLM 调用，Agent 的工作流、SQL 校验、SQL 执行仍然复用原实现。
    生成的 SQL 与回答均由简单规则产生，仅用于验证前端交互与接口契约，
    响应中会带 ``mock=true`` 标记。
    """

    TABLE_KEYWORDS = {
        "客户": "customers",
        "顾客": "customers",
        "账单": "invoices",
        "发票": "invoices",
        "歌曲": "tracks",
        "曲目": "tracks",
        "艺术家": "artists",
        "歌手": "artists",
        "员工": "employees",
        "专辑": "albums",
        "流派": "genres",
        "歌单": "playlists",
    }

    def chat(self, messages: list[dict], temperature: float | None = None) -> str:
        prompt = messages[-1]["content"]
        if "对话查询助手" in prompt:
            match = re.search(r"最新问题：([^\n]+)", prompt)
            question = match.group(1).strip() if match else ""
            # 模拟模式只做最小改写：上一轮在统计数量时，把“那 xxx 表呢”补全成独立问题
            if "呢" in question and re.search(r"多少|几条|数量|统计", prompt):
                table = re.search(r"[A-Za-z_][A-Za-z0-9_]*", question)
                if table:
                    return f"{table.group(0)} 表一共有多少条记录？"
            return question
        if "SQL 专家" in prompt or "SQL 修复专家" in prompt:
            return self._generate_sql(prompt)
        if "数据分析助手" in prompt:
            return self._summarize(prompt)
        return "（模拟模式）暂不支持该请求。"

    def chat_json(self, messages: list[dict]) -> dict[str, Any]:
        prompt = messages[-1]["content"]
        if "资深的数据库分析师" in prompt:
            return {"tables": self._pick_tables(prompt), "reason": "模拟模式关键词匹配"}
        if "SQL 审核专家" in prompt:
            return {"valid": True, "reason": "模拟模式默认通过"}
        return {"valid": True, "reason": "模拟模式"}

    # ------------------------------------------------------------- 规则实现
    def _pick_tables(self, prompt: str) -> list[str]:
        section = prompt.split("表间关系：")[0]
        candidates = re.findall(r"^- ([^\s（(，,]+)（", section, flags=re.M)
        question = _extract_prompt_field(prompt, "用户问题")
        for keyword, table in self.TABLE_KEYWORDS.items():
            if keyword in question and table in candidates:
                return [table]
        for name in candidates:
            if name in question:
                return [name]
        return candidates[:1] if candidates else []

    def _generate_sql(self, prompt: str) -> str:
        tables = re.findall(r"### 表 (\S+?)（", prompt)
        question = _extract_prompt_field(prompt, "用户问题")
        table = tables[0] if tables else "customers"

        if "国家" in question or "country" in question.lower():
            return (
                f'SELECT country, COUNT(*) AS 客户数 FROM "{table}" '
                f"GROUP BY country ORDER BY 客户数 DESC"
            )
        if "金额" in question and "total" in prompt.lower():
            return f'SELECT SUM(total) AS 总金额 FROM "{table}"'
        if any(word in question for word in ("多少", "几条", "数量", "总数", "统计")):
            return f'SELECT COUNT(*) AS 记录数 FROM "{table}"'
        limit = 10
        match = re.search(r"前\s*(\d+)\s*", question)
        if match:
            limit = max(1, min(int(match.group(1)), 100))
        return f'SELECT * FROM "{table}" LIMIT {limit}'

    def _summarize(self, prompt: str) -> str:
        match = re.search(r"查询结果（共 (\d+) 行", prompt)
        count = int(match.group(1)) if match else 0
        if count == 0:
            return "（模拟回答）没有查询到符合条件的数据。"
        return (
            f"（模拟回答）查询执行成功，共返回 {count} 行数据。"
            "当前处于离线模拟模式，SQL 与回答由本地规则生成，未调用真实大模型。"
        )


def _extract_prompt_field(prompt: str, label: str) -> str:
    match = re.search(
        rf"{label}：(.+?)(?:\n\n|\n要求|\n审核要点|\n当前 SQL|\n执行的 SQL|$)",
        prompt,
        re.S,
    )
    return match.group(1).strip() if match else ""


# ------------------------------------------------------------------ 构建 Agent
def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off"}


def build_agent() -> SQLAgent:
    """按环境变量构造真实 Agent；mock 模式下注入 MockLLM。"""
    db_path = os.getenv("SQL_AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
    engine = os.getenv("SQL_AGENT_ENGINE", "graph").strip().lower()
    mock = _env_flag("SQL_AGENT_MOCK", False)
    kwargs = dict(
        db_path=db_path,
        max_rewrite_rounds=int(os.getenv("SQL_AGENT_MAX_REWRITES", "2")),
        max_rows=int(os.getenv("SQL_AGENT_MAX_ROWS", "200")),
        semantic_check=_env_flag("SQL_AGENT_SEMANTIC_CHECK", True),
        llm=MockLLM() if mock else None,
    )
    if engine == "classic":
        return SQLAgent(**kwargs)
    from langgraph_agent import LangGraphSQLAgent

    return LangGraphSQLAgent(**kwargs)


# ------------------------------------------------------------------ 上下文处理
def _looks_like_followup(question: str) -> bool:
    if len(question) <= 12:
        return True
    return any(marker in question for marker in FOLLOWUP_MARKERS)


def _rewrite_question(agent: SQLAgent, history: list[Turn], question: str) -> str:
    """用模型把追问改写成独立问题；失败时回退到原问题。"""
    lines: list[str] = []
    for turn in history:
        lines.append(f"用户：{turn.question}")
        answer = turn.answer if len(turn.answer) <= 200 else turn.answer[:200] + "..."
        sql = turn.sql if len(turn.sql) <= 300 else turn.sql[:300] + "..."
        lines.append(f"助手：{answer}（SQL：{sql}）")

    prompt = (
        CONTEXT_REWRITE_PROMPT.replace("{#history#}", "\n".join(lines))
        .replace("{#question#}", question)
    )
    try:
        text = agent.llm.chat([{"role": "user", "content": prompt}], temperature=0.0)
    except Exception as exc:  # noqa: BLE001 - 改写失败不应阻断查询
        logger.warning("上下文改写失败，使用原问题：%s", exc)
        return question

    text = text.strip().strip('"').strip("“”")
    if not text or len(text) > 300:
        return question
    return text


def _run_chat(
    agent: SQLAgent, history: list[Turn], question: str
) -> tuple[Any, str, list[TraceStep], float]:
    """在工作线程中执行：上下文改写 + Agent 查询。"""
    started = time.perf_counter()
    resolved = question
    extra_trace: list[TraceStep] = []

    if history and _looks_like_followup(question):
        resolved = _rewrite_question(agent, history, question)
        extra_trace.append(
            TraceStep(
                "上下文理解",
                f"结合最近 {len(history)} 轮对话，改写为：{resolved}",
                {
                    "original_question": question,
                    "resolved_question": resolved,
                    "history_turns": len(history),
                },
            )
        )

    result = agent.ask(resolved)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    return result, resolved, extra_trace, elapsed_ms


# ---------------------------------------------------------------- Pydantic 模型
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000, description="用户问题")
    session_id: str | None = Field(
        default=None, description="会话 ID；不传则新建会话"
    )

    @field_validator("message")
    @classmethod
    def _strip_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value

    @field_validator("session_id")
    @classmethod
    def _check_session_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if value and not SESSION_ID_PATTERN.fullmatch(value):
            raise ValueError("session_id 只能包含字母、数字、下划线或短横线，长度 8~64")
        return value or None


class TraceStepModel(BaseModel):
    stage: str
    detail: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ChatResponse(BaseModel):
    session_id: str
    question: str
    resolved_question: str
    answer: str
    sql: str
    tables: list[str]
    attempts: int
    columns: list[str]
    rows: list[list[Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float
    mock: bool
    trace: list[TraceStepModel]


class TableSummary(BaseModel):
    name: str
    row_count: int


class HealthResponse(BaseModel):
    status: str
    mock: bool
    engine: str
    db_path: str
    model: str
    api_key_configured: bool
    timeout_seconds: float
    table_count: int
    tables: list[TableSummary]
    error: str | None = None


# -------------------------------------------------------------------- 应用工厂
def create_app(factory: Callable[[], SQLAgent] | None = None) -> FastAPI:
    runtime = AgentRuntime(factory or build_agent)
    store = SessionStore(
        max_sessions=int(os.getenv("WEB_MAX_SESSIONS", "200")),
        ttl_seconds=float(os.getenv("WEB_SESSION_TTL_MINUTES", "60")) * 60,
        max_turns=int(os.getenv("WEB_HISTORY_TURNS", "5")),
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.runtime = runtime
        app.state.store = store
        await runtime.initialize()
        yield
        await runtime.close()

    api = FastAPI(
        title="SQL Agent Web",
        version="1.0.0",
        description="SQL Agent 对话式 Web 接口（会话上下文 + 健康检查）",
        lifespan=lifespan,
    )

    # ---------------------------------------------------------- 错误映射
    @api.exception_handler(AgentUnavailable)
    async def _agent_unavailable(request: Request, exc: AgentUnavailable):
        return JSONResponse(
            status_code=503,
            content={"detail": f"Agent 不可用：{exc}", "code": "agent_unavailable"},
        )

    @api.exception_handler(RequestTimeout)
    async def _request_timeout(request: Request, exc: RequestTimeout):
        return JSONResponse(
            status_code=504,
            content={"detail": str(exc), "code": "timeout"},
        )

    @api.exception_handler(SQLAgentError)
    async def _sql_agent_error(request: Request, exc: SQLAgentError):
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc), "code": "sql_agent_error"},
        )

    @api.exception_handler(sqlite3.Error)
    async def _sqlite_error(request: Request, exc: sqlite3.Error):
        return JSONResponse(
            status_code=500,
            content={"detail": f"数据库错误：{exc}", "code": "database_error"},
        )

    @api.exception_handler(RuntimeError)
    async def _runtime_error(request: Request, exc: RuntimeError):
        return JSONResponse(
            status_code=502,
            content={"detail": str(exc), "code": "llm_error"},
        )

    @api.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError):
        return JSONResponse(
            status_code=400,
            content={"detail": str(exc), "code": "invalid_request"},
        )

    # ---------------------------------------------------------- 健康检查
    @api.get("/api/health", response_model=HealthResponse, tags=["system"])
    async def health():
        configured_db = os.getenv("SQL_AGENT_DB_PATH") or str(DEFAULT_DB_PATH)
        base = {
            "mock": runtime.mock,
            "db_path": configured_db,
            "model": os.getenv("SQL_AGENT_MODEL", "qwen-max"),
            "api_key_configured": bool(os.getenv("OPENAI_API_KEY")),
            "timeout_seconds": runtime.timeout,
        }
        if runtime.agent is None:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "engine": "unknown",
                    "table_count": 0,
                    "tables": [],
                    **base,
                    "error": runtime.agent_error or "Agent 未初始化",
                },
            )

        loop = asyncio.get_running_loop()
        try:
            info = await asyncio.wait_for(
                loop.run_in_executor(
                    runtime._executor, lambda: _health_sync(runtime.agent)
                ),
                timeout=10,
            )
        except asyncio.TimeoutError:
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "engine": type(runtime.agent).__name__,
                    "table_count": 0,
                    "tables": [],
                    **base,
                    "error": "数据库检查超时",
                },
            )
        return HealthResponse(status="ok", **info, **base)

    # ---------------------------------------------------------- 对话
    @api.post("/api/chat", response_model=ChatResponse, tags=["agent"])
    async def chat(payload: ChatRequest):
        session_id = payload.session_id or uuid.uuid4().hex
        history = store.history(session_id)

        result, resolved, extra_trace, elapsed_ms = await runtime.call(
            lambda agent: _run_chat(agent, history, payload.message)
        )

        store.append(
            session_id,
            Turn(
                question=payload.message,
                resolved=resolved,
                answer=result.answer,
                sql=result.sql,
            ),
        )

        return ChatResponse(
            session_id=session_id,
            question=payload.message,
            resolved_question=resolved,
            answer=result.answer,
            sql=result.sql,
            tables=result.tables,
            attempts=result.attempts,
            columns=result.columns,
            rows=result.rows,
            row_count=result.row_count,
            truncated=result.truncated,
            elapsed_ms=elapsed_ms,
            mock=runtime.mock,
            trace=[
                TraceStepModel(stage=s.stage, detail=s.detail, payload=s.payload)
                for s in list(extra_trace) + list(result.trace)
            ],
        )

    # ---------------------------------------------------------- 会话管理
    @api.delete("/api/session/{session_id}", tags=["session"])
    async def reset_session(session_id: str):
        if not SESSION_ID_PATTERN.fullmatch(session_id):
            return JSONResponse(
                status_code=400,
                content={"detail": "session_id 格式不合法", "code": "invalid_request"},
            )
        return {"session_id": session_id, "cleared": store.clear(session_id)}

    # ---------------------------------------------------------- 静态页面
    api.mount(
        "/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend"
    )
    return api


def _health_sync(agent: SQLAgent) -> dict[str, Any]:
    agent.parser.conn.execute("SELECT 1").fetchone()
    tables = [
        {"name": name, "row_count": agent.parser.get_row_count(name)}
        for name in agent.parser.table_names
    ]
    return {
        "engine": type(agent).__name__,
        "table_count": len(tables),
        "tables": tables,
        "error": None,
    }


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="SQL Agent Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="SQLite 数据库路径")
    parser.add_argument(
        "--mock",
        action="store_true",
        help="离线模拟模式：不调用真实大模型",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=None,
        help="单次对话超时秒数，默认 180",
    )
    args = parser.parse_args()

    if args.db:
        os.environ["SQL_AGENT_DB_PATH"] = args.db
    if args.mock:
        os.environ["SQL_AGENT_MOCK"] = "1"
    if args.timeout:
        os.environ["SQL_AGENT_TIMEOUT"] = str(args.timeout)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
