"""作业 2：网页聊天 + FastAPI + 作业 1 LangGraph。运行 python web_app.py。"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any, Callable, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from model_config import get_model_settings

from sql_agent import (
    DEFAULT_DB, DEMO_CASES, PROJECT_DIR, Backend, DemoBackend, OpenAICompatibleBackend,
    ReadOnlyDatabase, build_graph, initial_state,
)

LABELS = {
    "parse_database": "读取数据库结构", "select_tables": "选择相关数据表",
    "resolve_question": "理解连续追问",
    "generate_sql": "生成查询 SQL", "validate_sql": "校验 SQL",
    "execute_sql": "执行数据库查询", "repair_sql": "修复 SQL",
    "summarize": "整理查询回答", "handle_failure": "查询结束",
}
TABLE_LABELS = {
    "albums": "专辑", "artists": "艺术家", "customers": "客户", "employees": "员工",
    "genres": "音乐类型", "invoice_items": "发票明细", "invoices": "发票",
    "media_types": "媒体类型", "playlist_track": "播放列表曲目", "playlists": "播放列表",
    "tracks": "曲目",
}


class HistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=6000)


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(min_length=1, max_length=2000)
    history: list[HistoryMessage] = Field(default_factory=list, max_length=12)
    inject_bad_sql: bool = False

    @field_validator("question")
    @classmethod
    def nonempty_question(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("请输入要查询的问题")
        return value.strip()


def configured_mode() -> tuple[str, bool]:
    settings = get_model_settings()
    return settings.mode, settings.mode == "demo" or settings.ready


def event_line(kind: str, **payload: Any) -> str:
    return json.dumps({"type": kind, **payload}, ensure_ascii=False, default=str) + "\n"


def create_app(
    mode: str | None = None,
    database: ReadOnlyDatabase | None = None,
    backend_factory: Callable[[bool], Backend] | None = None,
    max_retries: int = 2,
) -> FastAPI:
    settings = get_model_settings()
    if mode is None:
        mode = settings.mode
    elif mode not in {"demo", "live"}:
        raise ValueError("mode 必须为 demo 或 live")
    ready = mode == "demo" or bool(backend_factory) or settings.ready
    db = database or ReadOnlyDatabase(DEFAULT_DB)
    app = FastAPI(title="Chinook Query", description="第13周作业2：SQL Agent 对话查询", version="1.0")
    app.state.mode = mode

    @app.get("/api/config")
    def config():
        return {"mode": mode, "ready": ready, "database": db.path.name,
                "provider": settings.provider, "provider_name": settings.provider_name,
                "model": settings.model if mode == "live" else None,
                "setup_hint": settings.setup_hint,
                "table_count": len(db.catalog()), "max_rows": db.max_rows,
                "examples": [case["question"] for case in DEMO_CASES]}

    @app.get("/api/health")
    def health():
        return {"status": "ok", "mode": mode, "ready": ready}

    @app.get("/api/schema")
    def schema():
        tables = []
        with db.connect() as conn:
            for name in db.catalog():
                # name 来自数据库自身目录，并转义为标识符，不拼接用户输入。
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [dict(row) for row in conn.execute(f"PRAGMA table_info({quoted})")]
                count = conn.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                tables.append({"name": name, "label": TABLE_LABELS.get(name, name),
                               "row_count": count, "columns": columns})
        return {"database": db.path.name, "tables": tables}

    @app.post("/api/chat")
    def chat(request: ChatRequest):
        if not ready:
            raise HTTPException(503, settings.setup_hint)
        if mode != "demo" and request.inject_bad_sql:
            raise HTTPException(400, "故障演示仅适用于离线演示模式")
        if sum(len(item.content) for item in request.history) > 30000:
            raise HTTPException(422, "历史对话过长，请开始新对话")
        try:
            backend = (backend_factory(request.inject_bad_sql) if backend_factory else
                       DemoBackend(request.inject_bad_sql) if mode == "demo" else
                       OpenAICompatibleBackend(settings))
        except Exception:
            raise HTTPException(503, "模型服务初始化失败，请检查服务端配置") from None
        graph = build_graph(db, backend, max_retries)

        def stream():
            started = time.monotonic()
            state = initial_state(request.question, [item.model_dump() for item in request.history])
            yield event_line("start", mode=mode)
            try:
                for chunk in graph.stream(state, config={"recursion_limit": 20 + 4 * max_retries},
                                          stream_mode="updates"):
                    for name, update in chunk.items():
                        state.update(update)
                        yield event_line("node", node=name, label=LABELS.get(name, name),
                                         failed=bool(update.get("error")),
                                         retry_count=state["retry_count"])
                # 返回可核对的执行结果，不把完整表目录或配置发给网页。
                output = {key: state[key] for key in (
                    "question", "resolved_question", "sql", "results", "summary", "selected_tables", "status",
                    "retry_count", "fails", "error", "truncated", "trace",
                )}
                yield event_line("done", result={**output, "mode": mode,
                                                "elapsed_ms": round((time.monotonic() - started) * 1000)})
            except Exception:
                # 流已开始时，错误也通过明确事件传递；客户端不能把半截流视为成功。
                yield event_line("error", message="查询过程意外中断，请重试或检查后端日志")

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(PROJECT_DIR / "web" / "index.html")

    app.mount("/static", StaticFiles(directory=PROJECT_DIR / "web"), name="static")
    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true", help="强制使用离线固定案例")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8013)
    args = parser.parse_args()
    uvicorn.run(create_app(mode="demo" if args.demo else None), host=args.host, port=args.port)
