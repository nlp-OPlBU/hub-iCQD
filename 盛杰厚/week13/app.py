"""
SQL Chat —— 用网页和 chinook 数据库「聊天」（后端）

功能：
  - 提供前端页面（static/index.html）
  - 把用户的一句话交给 sql_agent_langgraph.SQLAgent（LangGraph 图）执行：
    表选择 -> SQL 生成 -> 校验 -> 改写 -> 执行 -> 汇总
  - 通过 SSE 把每个节点的执行进度实时推给前端，最后返回自然语言回答 + SQL + 结果表格
  - 以 session_id 作为 LangGraph 的 thread_id，同一会话的多次提问自动带上多轮上下文

运行：
  conda activate py312
  uvicorn app:app --host 0.0.0.0 --port 8001
  浏览器打开 http://localhost:8001

配置（环境变量 或 同目录 .env 文件，环境变量优先）：
  DEEPSEEK_API_KEY   模型服务 API Key（必填，除非 MOCK=1）
  DEEPSEEK_BASE_URL  OpenAI 兼容接口地址，默认 https://api.deepseek.com
  DEEPSEEK_MODEL     模型名称，默认 deepseek-flash
  DB_URL             数据库连接地址，默认本目录的 chinook.db
  MOCK               设为 1 时不调用大模型，用本地规则模拟，便于无 Key 调试前后端
"""

import os
import re
import json
import time
import uuid
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from sql_agent_langgraph import SQLAgent, DEEPSEEK_BASE_URL, DEFAULT_MODEL

BASE_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
def load_dotenv(path: Path) -> None:
    """极简 .env 解析，避免引入额外依赖。已存在的环境变量优先级更高。"""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


load_dotenv(BASE_DIR / ".env")

API_KEY = (os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("API_KEY") or "").strip()
BASE_URL = (os.environ.get("DEEPSEEK_BASE_URL") or os.environ.get("API_BASE_URL") or DEEPSEEK_BASE_URL).strip()
MODEL_NAME = (os.environ.get("DEEPSEEK_MODEL") or os.environ.get("MODEL_NAME") or DEFAULT_MODEL).strip()
DB_URL = os.environ.get("DB_URL") or f"sqlite:///{BASE_DIR / 'chinook.db'}"
MOCK = os.environ.get("MOCK", "0").strip() in ("1", "true", "True", "yes")
MAX_RETRY = int(os.environ.get("MAX_RETRY", "5"))

MAX_MESSAGE_CHARS = 500
MAX_ROWS_TO_CLIENT = 200


# ---------------------------------------------------------------------------
# 无 Key 调试：用本地规则模拟大模型的三个输出（表选择 / SQL / 汇总）
# ---------------------------------------------------------------------------
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

# (关键词, 选中的表,  canned SQL)
MOCK_RULES = [
    (
        ("客户", "消费", "花费", "customer"),
        ["customers", "invoices"],
        "SELECT c.CustomerId, c.FirstName || ' ' || c.LastName AS Customer, "
        "ROUND(SUM(i.Total), 2) AS TotalSpent "
        "FROM customers c JOIN invoices i ON i.CustomerId = c.CustomerId "
        "GROUP BY c.CustomerId ORDER BY TotalSpent DESC LIMIT 10;",
    ),
    (
        ("国家", "country"),
        ["invoices", "customers"],
        "SELECT BillingCountry AS Country, ROUND(SUM(Total), 2) AS Sales "
        "FROM invoices GROUP BY BillingCountry ORDER BY Sales DESC LIMIT 10;",
    ),
    (
        ("流派", "风格", "genre", "类型"),
        ["genres", "tracks"],
        "SELECT g.Name AS Genre, COUNT(t.TrackId) AS Tracks "
        "FROM genres g JOIN tracks t ON t.GenreId = g.GenreId "
        "GROUP BY g.GenreId ORDER BY Tracks DESC LIMIT 10;",
    ),
    (
        ("歌手", "艺术家", "artist"),
        ["artists", "albums", "tracks"],
        "SELECT ar.Name AS Artist, COUNT(t.TrackId) AS Tracks "
        "FROM artists ar JOIN albums al ON al.ArtistId = ar.ArtistId "
        "JOIN tracks t ON t.AlbumId = al.AlbumId "
        "GROUP BY ar.ArtistId ORDER BY Tracks DESC LIMIT 10;",
    ),
    (
        ("专辑", "album"),
        ["albums", "tracks"],
        "SELECT al.Title AS Album, COUNT(t.TrackId) AS Tracks "
        "FROM albums al JOIN tracks t ON t.AlbumId = al.AlbumId "
        "GROUP BY al.AlbumId ORDER BY Tracks DESC LIMIT 10;",
    ),
    (
        ("员工", "销售员", "employee"),
        ["employees", "customers"],
        "SELECT e.FirstName || ' ' || e.LastName AS Employee, COUNT(c.CustomerId) AS Customers "
        "FROM employees e LEFT JOIN customers c ON c.SupportRepId = e.EmployeeId "
        "GROUP BY e.EmployeeId ORDER BY Customers DESC LIMIT 10;",
    ),
    (
        ("播放列表", "playlist"),
        ["playlists", "playlist_track", "tracks"],
        "SELECT p.Name AS Playlist, COUNT(pt.TrackId) AS Tracks "
        "FROM playlists p JOIN playlist_track pt ON pt.PlaylistId = p.PlaylistId "
        "GROUP BY p.PlaylistId ORDER BY Tracks DESC LIMIT 10;",
    ),
    (
        ("发票", "invoice"),
        ["invoices"],
        "SELECT strftime('%Y', InvoiceDate) AS Year, COUNT(*) AS Invoices, "
        "ROUND(SUM(Total), 2) AS Sales FROM invoices "
        "GROUP BY Year ORDER BY Year LIMIT 10;",
    ),
    (
        ("歌曲", "曲目", "track", "歌"),
        ["tracks"],
        "SELECT t.Name AS Track, ROUND(SUM(il.UnitPrice * il.Quantity), 2) AS Sales "
        "FROM tracks t JOIN invoice_items il ON il.TrackId = t.TrackId "
        "GROUP BY t.TrackId ORDER BY Sales DESC LIMIT 10;",
    ),
]


class MockChatModel(BaseChatModel):
    """MOCK=1 时替代 ChatOpenAI：看清提示词是哪一步，返回对应的模拟结果。"""

    @property
    def _llm_type(self) -> str:
        return "mock-sql-agent"

    @staticmethod
    def _ask(prompt: str) -> str:
        m = re.search(r"用户提问：(.*?)(?:\n\n|$)", prompt, re.S)
        return (m.group(1).strip() if m else "").strip()

    @staticmethod
    def _pick(question: str):
        lowered = question.lower()
        for keywords, tables, sql in MOCK_RULES:
            if any(k in lowered for k in keywords):
                return tables, sql
        return ["invoices", "customers"], "SELECT COUNT(*) AS Invoices, ROUND(SUM(Total), 2) AS Sales FROM invoices;"

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        prompt = "\n".join(str(getattr(m, "content", "")) for m in messages)
        question = self._ask(prompt)
        tables, sql = self._pick(question)

        # 1) 表选择：提示词要求返回 JSON
        if "数据库中的所有表名" in prompt:
            text = json.dumps({"tables": tables}, ensure_ascii=False)
        # 2) 结果汇总：提示词里有「答案汇总专家」
        elif "答案汇总专家" in prompt:
            rows = re.findall(r"^[^|\n]+\|[^\n]*$", prompt.split("执行结果：")[-1], re.M)
            n = max(len(rows) - 1, 0)  # 去掉表头
            text = f"（模拟回答）查询共返回 {n} 行结果，详细数据见下方执行结果表格。"
        # 3) SQL 生成 / SQL 改写：返回 canned SQL，并按「前 N」调整 LIMIT
        else:
            m = re.search(r"前\s*(\d+)|top\s*(\d+)", question, re.I)
            n = (m.group(1) or m.group(2)) if m else None
            text = re.sub(r"LIMIT \d+", f"LIMIT {n}", sql) if n and "LIMIT" in sql else sql

        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=text))])


# ---------------------------------------------------------------------------
# Agent 单例（数据库解析比较慢，只做一次）
# ---------------------------------------------------------------------------
_agent: Optional[SQLAgent] = None
_agent_lock = threading.Lock()


def build_agent() -> SQLAgent:
    if MOCK:
        llm = MockChatModel()
        return SQLAgent(DB_URL, llm=llm, summary_llm=llm, max_retry=MAX_RETRY, verbose=False)
    return SQLAgent(
        DB_URL,
        api_key=API_KEY,
        base_url=BASE_URL,
        model=MODEL_NAME,
        max_retry=MAX_RETRY,
        verbose=False,
    )


def get_agent() -> SQLAgent:
    global _agent
    if _agent is None:
        with _agent_lock:
            if _agent is None:
                _agent = build_agent()
    return _agent


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def jsonable(value: Any) -> Any:
    """把 SQL 结果里的非 JSON 类型（bytes / datetime / Decimal）转成可序列化的值。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        return isoformat()
    return str(value)


def serialize_result(state: Dict[str, Any]) -> Dict[str, Any]:
    rows = state.get("rows") or []
    columns = state.get("columns") or []
    return {
        "answer": state.get("answer") or "",
        "sql": state.get("sql") or "",
        "tables": state.get("tables") or [],
        "validated": bool(state.get("validated")),
        "failed": bool(state.get("failed")),
        "columns": columns,
        "rows": [[jsonable(v) for v in row] for row in rows[:MAX_ROWS_TO_CLIENT]],
        "row_count": len(rows),
        "truncated": len(rows) > MAX_ROWS_TO_CLIENT,
        "retries": int(state.get("retries") or 0),
        "steps": state.get("steps") or [],
    }


def sse(event: str, payload: Dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
app = FastAPI(title="SQL Chat · Chinook 对话查询", version="1.0.0")
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


class ResetRequest(BaseModel):
    session_id: str


@app.get("/")
def index() -> FileResponse:
    return FileResponse(str(BASE_DIR / "static" / "index.html"))


@app.get("/api/health")
def health() -> Dict[str, Any]:
    return {
        "status": "ok",
        "model": "mock" if MOCK else MODEL_NAME,
        "api_base_url": BASE_URL,
        "mock": MOCK,
        "api_key_configured": bool(API_KEY),
        "db_url": DB_URL,
        "max_retry": MAX_RETRY,
    }


@app.get("/api/schema")
def schema() -> Dict[str, Any]:
    """给前端「数据表」面板用的库结构。"""
    agent = get_agent()
    return {
        "tables": agent.parser.schema_summary(),
        "relations": agent.parser.describe_relations().splitlines(),
    }


def _validate(req: ChatRequest) -> str:
    message = (req.message or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="请输入问题")
    if len(message) > MAX_MESSAGE_CHARS:
        raise HTTPException(status_code=400, detail=f"问题过长（最多 {MAX_MESSAGE_CHARS} 字）")
    if not MOCK and not API_KEY:
        raise HTTPException(
            status_code=500,
            detail="未配置 API Key。请设置环境变量 DEEPSEEK_API_KEY（或在 .env 中填写），"
                   "或设置 MOCK=1 进行本地模拟调试。",
        )
    session_id = (req.session_id or "default").strip() or "default"
    return session_id


@app.post("/api/chat")
def chat(req: ChatRequest) -> Dict[str, Any]:
    """一次性返回完整结果（不流式）。"""
    session_id = _validate(req)
    started = time.time()
    try:
        state = get_agent().action(req.message.strip(), verbose=False, thread_id=session_id)
    except Exception as exc:  # 数据库/图执行异常
        raise HTTPException(status_code=502, detail=f"执行失败：{exc}") from exc
    result = serialize_result(state)
    result["elapsed_ms"] = int((time.time() - started) * 1000)
    return result


@app.post("/api/chat/stream")
def chat_stream(req: ChatRequest) -> StreamingResponse:
    """SSE：每个节点执行完推一条 step，最后推一条 done（含完整结果）。"""
    session_id = _validate(req)
    message = req.message.strip()

    def event_stream():
        started = time.time()
        yield sse("start", {"session_id": session_id, "message": message})
        try:
            agent = get_agent()
            for node_name, node_state in agent.run_stream(message, thread_id=session_id, verbose=False):
                # begin / remember 是图内部的记账节点，不展示到前端进度条
                if node_name in ("begin", "remember"):
                    continue
                steps = node_state.get("steps") or []
                step = steps[-1] if steps else {"node": node_name, "title": node_name, "status": "ok"}
                yield sse("step", step)
            state = agent.get_state(session_id)
            result = serialize_result(state)
            result["elapsed_ms"] = int((time.time() - started) * 1000)
            yield sse("done", result)
        except Exception as exc:
            yield sse("error", {"message": f"执行失败：{exc}"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/reset")
def reset(req: ResetRequest) -> Dict[str, Any]:
    """清空某个会话的对话记忆（前端点「新对话」时调用）。"""
    session_id = (req.session_id or "").strip()
    if session_id:
        try:
            get_agent().reset_thread(session_id)
        except Exception:
            pass  # 没有 checkpointer 删除能力时忽略，前端会换新的 session_id
    return {"status": "ok", "session_id": session_id or uuid.uuid4().hex}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=8001, reload=True)
