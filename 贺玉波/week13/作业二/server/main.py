# -*- coding: utf-8 -*-
"""FastAPI 后端：把 LangGraph agent 的一次执行过程完整回传给前端。

工程要点：
- `sql_agent_lg` 包同时被 CLI（run.py）和 API 复用，不复制第二套编排逻辑；
- `/api/chat` 返回 result（同 week12 CLI 字段）+ trace（节点轨迹）+ result_table；
- HITL：clarify 节点用 `interrupt()` 暂停，`/api/chat` 返回 status=interrupted 与
  thread_id；`/api/chat/resume` 用 `Command(resume=answer)` 从检查点继续；
- 无 API key 时自动建议 mock；请求级 `mock` 字段可覆盖。
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from sql_agent_lg.config import settings
from sql_agent_lg.graph import AgentRun, SQLAgentGraph, ThreadNotFoundError

BASE_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = BASE_DIR / "web"

GRAPH_NODES = [
    "select_tables",
    "generate_sql",
    "validate_sql",
    "execute_sql",
    "repair_sql",
    "summarize",
    "clarify",
    "fail",
]

logger = logging.getLogger("week13")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

app = FastAPI(title="LangGraph SQL Agent", version="1.0")

# HITL 检查点必须跨请求共享：/api/chat 中断后，/api/chat/resume 才能在同一个
# thread_id 上恢复。这里用进程内 MemorySaver（演示足够；生产可换 Redis/SQLite）。
CHECKPOINTER = MemorySaver()


class Turn(BaseModel):
    question: str = ""
    answer: str = ""


class ChatRequest(BaseModel):
    question: str = Field(description="当前问题")
    history: list[Turn] = Field(default_factory=list, description="最近几轮问答，用于多轮上下文")
    mock: bool | None = Field(default=None, description="是否使用离线 Mock；不填则读 SQL_AGENT_MOCK")
    sql: str | None = Field(default=None, description="直接给定 SQL，跳过选表/生成节点")
    tables: list[str] | None = Field(default=None, description="配合 sql 指定涉及的表")
    max_repair_attempts: int | None = Field(default=None, ge=0, le=5)


class ResumeRequest(BaseModel):
    thread_id: str = Field(description="上一次 /api/chat 返回的 thread_id")
    answer: str = Field(description="用户对澄清问题的回答，可直接使用选项文本")
    mock: bool | None = Field(default=None, description="与首次请求保持一致；不填则读 SQL_AGENT_MOCK")
    max_repair_attempts: int | None = Field(default=None, ge=0, le=5)


@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error": {"code": f"http_{exc.status_code}", "message": str(exc.detail)}},
    )


@app.exception_handler(Exception)
async def handle_unexpected(request: Request, exc: Exception):
    logger.exception("未处理异常 path=%s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"ok": False, "error": {"code": "server_error", "message": str(exc)}},
    )


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "model": settings.model,
        "base_url": settings.base_url,
        "db": settings.db_path.name,
        "db_exists": settings.db_path.is_file(),
        "key_configured": settings.key_configured,
        "mock_default": settings.mock,
        "mock_scenario": settings.mock_scenario,
        "max_repair_attempts": settings.max_repair_attempts,
        "graph_nodes": GRAPH_NODES,
        "hitl": {"enabled": True, "node": "clarify", "resume_endpoint": "/api/chat/resume"},
    }


def _require_key(use_mock: bool) -> None:
    if not use_mock and not settings.key_configured:
        raise HTTPException(
            status_code=503,
            detail="后端未配置 DEEPSEEK_API_KEY。请在 .env 填入密钥，或开启 mock 模式。",
        )


def _build_response(
    run: AgentRun, use_mock: bool, elapsed_ms: int, max_repair_attempts: int
) -> dict:
    result_table = None
    if run.execution is not None:
        result_table = {
            "columns": run.execution.columns,
            "rows": run.execution.rows,
            "row_count": run.execution.row_count,
            "truncated": run.execution.truncated,
        }
    return {
        "ok": True,
        "status": run.status,
        "thread_id": run.thread_id,
        "interrupt": run.interrupt,
        "result": run.output.model_dump(mode="json"),
        "trace": run.trace,
        "result_table": result_table,
        "meta": {
            "provider": "mock" if use_mock else "deepseek",
            "model": settings.model,
            "mock": use_mock,
            "max_repair_attempts": max_repair_attempts,
            "elapsed_ms": elapsed_ms,
        },
    }


@app.post("/api/chat")
async def chat(payload: ChatRequest):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="问题不能为空")
    if len(question) > 2000:
        raise HTTPException(status_code=400, detail="问题过长（上限 2000 字）")

    use_mock = settings.mock if payload.mock is None else payload.mock
    _require_key(use_mock)

    started = time.perf_counter()
    agent = SQLAgentGraph(
        mock=use_mock,
        mock_scenario=settings.mock_scenario,
        max_repair_attempts=payload.max_repair_attempts,
        checkpointer=CHECKPOINTER,
    )
    run = await agent.start_run(
        question,
        history=[turn.model_dump() for turn in payload.history],
        sql=payload.sql,
        tables=payload.tables,
    )
    if not run.interrupted:
        agent.delete_thread(run.thread_id)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "chat status=%s question=%s mock=%s rows=%s repairs=%s elapsed=%dms",
        run.status,
        question[:80],
        use_mock,
        run.output.row_count,
        run.output.repair_attempts,
        elapsed_ms,
    )
    return _build_response(run, use_mock, elapsed_ms, agent.max_repair_attempts)


@app.post("/api/chat/resume")
async def resume(payload: ResumeRequest):
    thread_id = payload.thread_id.strip()
    answer = payload.answer.strip()
    if not thread_id:
        raise HTTPException(status_code=400, detail="thread_id 不能为空")
    if not answer:
        raise HTTPException(status_code=400, detail="澄清回答不能为空")

    use_mock = settings.mock if payload.mock is None else payload.mock
    _require_key(use_mock)

    started = time.perf_counter()
    agent = SQLAgentGraph(
        mock=use_mock,
        mock_scenario=settings.mock_scenario,
        max_repair_attempts=payload.max_repair_attempts,
        checkpointer=CHECKPOINTER,
    )
    try:
        run = await agent.resume_run(answer, thread_id=thread_id)
    except ThreadNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 成功完成后清理检查点；失败时保留，便于前端用同一个 thread_id 重试。
    agent.delete_thread(thread_id)

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "resume status=%s thread=%s answer=%s mock=%s rows=%s elapsed=%dms",
        run.status,
        thread_id,
        answer[:60],
        use_mock,
        run.output.row_count,
        elapsed_ms,
    )
    return _build_response(run, use_mock, elapsed_ms, agent.max_repair_attempts)


# 静态页面最后挂载，避免把 /api 路由吃掉
app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
