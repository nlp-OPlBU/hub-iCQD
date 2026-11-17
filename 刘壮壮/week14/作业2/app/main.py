import hashlib
import base64
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import asyncio
import time
import hmac
from collections import defaultdict, deque
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.db import connect, init_db
from app.services import adapter_status, now, uid
from app.worker.dispatcher import OutboxDispatcher
from app.clients.elasticsearch import check_health as check_elasticsearch
from app.retrieval import search as hybrid_search
from app.llm import estimate_text_tokens, stream_answer

app = FastAPI(title="Multimodal RAG API", version="0.1.0", description="Backend API for PDF ingestion and multimodal RAG")
_request_times: dict[str, deque[float]] = defaultdict(deque)


@app.middleware("http")
async def request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or uid("req")
    settings = get_settings()
    idem=request.headers.get('Idempotency-Key')
    guarded=request.method in ('POST','DELETE') and any(x in request.url.path for x in ('/documents','/messages','/conversations'))
    if idem and guarded:
        try:
            with connect() as db: db.execute("INSERT INTO idempotency_keys(key,route,created_at) VALUES(?,?,?)",(idem,request.url.path,now()))
        except sqlite3.IntegrityError:
            with connect() as db: cached=db.execute("SELECT response_status,response_body,content_type FROM idempotency_keys WHERE key=? AND route=?",(idem,request.url.path)).fetchone()
            if cached and cached['response_body'] is not None:
                return Response(content=cached['response_body'],status_code=cached['response_status'],media_type=cached['content_type'],headers={"X-Request-ID":request.state.request_id})
            return JSONResponse(status_code=409,headers={"X-Request-ID":request.state.request_id},content={"success":False,"data":None,"error":{"code":"IDEMPOTENT_REPLAY","message":"此操作仍在处理或已处理","details":None}})
    if request.url.path.startswith('/api/v1/') and request.url.path != '/api/v1/health':
        key=request.client.host if request.client else 'unknown'; now_mono=time.monotonic(); bucket=_request_times[key]
        while bucket and bucket[0] < now_mono-60: bucket.popleft()
        if len(bucket) >= settings.rate_limit_per_minute:
            return JSONResponse(status_code=429,headers={"X-Request-ID":request.state.request_id,"Retry-After":"60"},content={"success":False,"data":None,"error":{"code":"RATE_LIMITED","message":"请求过于频繁，请稍后重试","details":None}})
        bucket.append(now_mono)
    if settings.api_key and request.url.path.startswith("/api/v1/") and request.url.path != "/api/v1/health":
        supplied = request.headers.get("X-API-Key", "")
        if not hmac.compare_digest(supplied, settings.api_key):
            return JSONResponse(status_code=401, headers={"X-Request-ID": request.state.request_id}, content={"success":False,"data":None,"error":{"code":"API_KEY_REQUIRED","message":"API key required","details":None}})
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    if idem and guarded and response.headers.get('content-type','').startswith('application/json') and response.status_code < 500:
        body=b''.join([chunk async for chunk in response.body_iterator])
        content_type=response.headers.get('content-type','application/json')
        with connect() as db: db.execute("UPDATE idempotency_keys SET response_status=?,response_body=?,content_type=? WHERE key=? AND route=?",(response.status_code,body,content_type,idem,request.url.path))
        return Response(content=body,status_code=response.status_code,headers={k:v for k,v in response.headers.items() if k.lower() not in ('content-length','transfer-encoding','content-type')},media_type=content_type)
    return response


@app.exception_handler(HTTPException)
async def api_error(_: Request, exc: HTTPException):
    code = str(exc.detail) if isinstance(exc.detail, str) else "REQUEST_FAILED"
    return JSONResponse(status_code=exc.status_code, content={"success": False, "data": None,
                          "error": {"code": code, "message": code, "details": None}})


@app.on_event("startup")
async def startup():
    init_db()
    if get_settings().adapter_mode == "real":
        dispatcher = OutboxDispatcher()
        app.state.dispatcher_task = asyncio.create_task(dispatcher.run())


@app.on_event("shutdown")
async def shutdown():
    task = getattr(app.state, "dispatcher_task", None)
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


def ok(data, status_code=200):
    return JSONResponse(status_code=status_code, content={"success": True, "data": data, "error": None})


def rowdict(row):
    return dict(row) if row else None


@app.get("/api/v1/health")
def health():
    try:
        with connect() as db:
            db.execute("SELECT 1")
        db_status = "ok"
    except sqlite3.Error:
        db_status = "error"
    adapters = adapter_status()
    if get_settings().adapter_mode == "real":
        adapters["elasticsearch"] = check_elasticsearch()
    return ok({"status": "ok" if db_status == "ok" else "degraded", "database": db_status, "adapters": adapters})


@app.get('/api/v1/health/dependencies')
def dependency_health():
    settings=get_settings(); results={"sqlite":"ok"}
    if settings.adapter_mode!='real':
        return ok({"checked_at":now(),"services":{"sqlite":"ok","kafka":"not_configured","mineru":"not_configured","elasticsearch":"not_configured","llm":"not_configured"}})
    try: results['elasticsearch']=check_elasticsearch()
    except Exception: results['elasticsearch']='error'
    for name,url,headers in [('mineru',settings.mineru_api_url,{'Authorization':f'Bearer {settings.mineru_api_key}'}),('llm',settings.deepseek_api_url,{'Authorization':f'Bearer {settings.deepseek_api_key}'})]:
        try:
            import httpx
            response=httpx.get(url,headers=headers,timeout=3) if url else None
            results[name]='ok' if response and response.status_code<500 else 'error'
        except Exception: results[name]='error'
    try:
        from kafka import KafkaAdminClient
        admin=KafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers,request_timeout_ms=3000); admin.close(); results['kafka']='ok'
    except Exception: results['kafka']='error'
    return ok({"checked_at":now(),"services":results})


@app.post("/api/v1/documents")
async def upload_documents(files: Annotated[list[UploadFile], File(description="PDF files")]):
    settings = get_settings()
    if not files or len(files) > settings.max_upload_files:
        raise HTTPException(422, "UPLOAD_FILE_COUNT_INVALID")
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    results = []
    for upload in files:
        name = Path(upload.filename or "document.pdf").name
        if not name.lower().endswith(".pdf") or (upload.content_type not in (None, "application/pdf", "application/octet-stream")):
            raise HTTPException(422, "PDF_REQUIRED")
        document_uid, task_uid = uid("doc"), uid("task")
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:150] or "document.pdf"
        destination = settings.storage_path / f"{document_uid}_{safe_name}"
        digest = hashlib.sha256()
        size = 0
        try:
            with destination.open("wb") as out:
                while chunk := await upload.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise HTTPException(413, "PDF_TOO_LARGE")
                    digest.update(chunk)
                    out.write(chunk)
            if size == 0:
                raise HTTPException(422, "EMPTY_FILE")
            timestamp = now()
            with connect() as db:
                cur = db.execute("INSERT INTO documents(document_uid,filename,file_path,file_size,file_hash,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
                                 (document_uid, name, str(destination), size, digest.hexdigest(), timestamp, timestamp))
                document_id = cur.lastrowid
                cur = db.execute("INSERT INTO document_versions(document_id,version_number,status,created_at) VALUES(?,1,'pending',?)", (document_id, timestamp))
                version_id = cur.lastrowid
                db.execute("UPDATE documents SET current_version_id=? WHERE id=?", (version_id, document_id))
                db.execute("INSERT INTO parse_tasks(task_uid,document_id,document_version_id,status,created_at,updated_at) VALUES(?,?,?,'pending',?,?)",
                           (task_uid, document_id, version_id, timestamp, timestamp))
                task_id = db.execute("SELECT id FROM parse_tasks WHERE task_uid=?", (task_uid,)).fetchone()["id"]
                event_uid = uid("evt")
                payload = json.dumps({"event_uid": event_uid, "event_type": "document.parse.requested", "schema_version": 1, "task_uid": task_uid, "created_at": timestamp})
                db.execute("INSERT INTO task_outbox(event_uid,task_id,event_type,topic,payload,status,created_at,updated_at) VALUES(?,?,? ,? ,?,'pending',?,?)",
                           (event_uid, task_id, "document.parse.requested", settings.kafka_topic, payload, timestamp, timestamp))
            results.append({"document_uid": document_uid, "filename": name, "status": "pending", "task_uid": task_uid})
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()
    return ok({"items": results, "count": len(results)}, 201)


@app.get("/api/v1/documents")
def list_documents(page: int = 1, page_size: int = 20, status: str | None = None, q: str | None = None):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422, "PAGINATION_INVALID")
    with connect() as db:
        where, params = ("WHERE status=? AND filename LIKE ?", [status, f"%{q}%"]) if status else (("WHERE filename LIKE ?", [f"%{q}%"]) if q else ("", []))
        total = db.execute(f"SELECT count(*) FROM documents {where}", params).fetchone()[0]
        rows = db.execute(f"SELECT document_uid,filename,file_size,status,current_version_id,created_at,updated_at FROM documents {where} ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?", [*params, page_size, (page-1)*page_size]).fetchall()
    return ok({"items": [dict(r) for r in rows], "total": total, "page": page, "page_size": page_size})


def create_outbox_event(db, task_id: int, task_uid: str, timestamp: str):
    settings = get_settings()
    event_uid = uid("evt")
    payload = json.dumps({"event_uid":event_uid,"event_type":"document.parse.requested","schema_version":1,
                          "task_uid":task_uid,"created_at":timestamp})
    db.execute("INSERT INTO task_outbox(event_uid,task_id,event_type,topic,payload,status,created_at,updated_at) VALUES(?,?,?, ?,?,'pending',?,?)",
               (event_uid,task_id,"document.parse.requested",settings.kafka_topic,payload,timestamp,timestamp))


@app.get("/api/v1/documents/{document_uid}/tasks")
def list_document_tasks(document_uid: str, page: int = 1, page_size: int = 20):
    if page < 1 or not 1 <= page_size <= 100:
        raise HTTPException(422,"PAGINATION_INVALID")
    with connect() as db:
        document=db.execute("SELECT id FROM documents WHERE document_uid=?",(document_uid,)).fetchone()
        if not document: raise HTTPException(404,"DOCUMENT_NOT_FOUND")
        total=db.execute("SELECT count(*) FROM parse_tasks WHERE document_id=?",(document["id"],)).fetchone()[0]
        rows=db.execute("SELECT task_uid,document_version_id,status,retry_count,error_type,last_error,created_at,started_at,completed_at FROM parse_tasks WHERE document_id=? ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                        (document["id"],page_size,(page-1)*page_size)).fetchall()
    return ok({"items":[dict(r) for r in rows],"total":total,"page":page,"page_size":page_size})


@app.get("/api/v1/documents/{document_uid}/assets/{asset_id}")
def get_document_asset(document_uid: str, asset_id: int):
    with connect() as db:
        row=db.execute("""SELECT a.file_path,a.mime_type FROM document_assets a
            JOIN document_versions v ON v.id=a.document_version_id JOIN documents d ON d.id=v.document_id
            WHERE d.document_uid=? AND a.id=?""",(document_uid,asset_id)).fetchone()
    if not row: raise HTTPException(404,"ASSET_NOT_FOUND")
    if not Path(row["file_path"]).is_file(): raise HTTPException(404,"ASSET_FILE_NOT_FOUND")
    return FileResponse(row["file_path"],media_type=row["mime_type"],filename=Path(row["file_path"]).name)


@app.post("/api/v1/documents/{document_uid}/reparse")
def reparse_document(document_uid: str):
    timestamp=now(); task_uid=uid("task")
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        document=db.execute("SELECT * FROM documents WHERE document_uid=?",(document_uid,)).fetchone()
        if not document: raise HTTPException(404,"DOCUMENT_NOT_FOUND")
        if document["status"] == "deleting": raise HTTPException(409,"DOCUMENT_DELETING")
        active=db.execute("SELECT 1 FROM parse_tasks WHERE document_id=? AND status IN ('pending','processing') LIMIT 1",(document["id"],)).fetchone()
        if active: raise HTTPException(409,"PARSE_ALREADY_RUNNING")
        version_number=db.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM document_versions WHERE document_id=?",(document["id"],)).fetchone()[0]
        version_id=db.execute("INSERT INTO document_versions(document_id,version_number,status,created_at) VALUES(?,?,'pending',?)",
                              (document["id"],version_number,timestamp)).lastrowid
        task_id=db.execute("INSERT INTO parse_tasks(task_uid,document_id,document_version_id,status,execution_stage,created_at,updated_at) VALUES(?,?,?,'pending','queued',?,?)",
                           (task_uid,document["id"],version_id,timestamp,timestamp)).lastrowid
        db.execute("UPDATE documents SET status='processing',updated_at=? WHERE id=?",(timestamp,document["id"]))
        create_outbox_event(db,task_id,task_uid,timestamp)
    return ok({"task_uid":task_uid,"version_number":version_number,"status":"pending"},202)


@app.delete("/api/v1/documents/{document_uid}")
def delete_document(document_uid: str):
    # Marking first fences new Outbox claims and lets an in-flight Worker detect cancellation.
    with connect() as db:
        document=db.execute("SELECT * FROM documents WHERE document_uid=?",(document_uid,)).fetchone()
        if not document: raise HTTPException(404,"DOCUMENT_NOT_FOUND")
        document_id=document["id"]
        db.execute("UPDATE documents SET status='deleting',updated_at=? WHERE id=?",(now(),document_id))
        db.execute("UPDATE parse_tasks SET status='cancelled',execution_stage='cancelled',lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE document_id=? AND status IN ('pending','processing')",
                   (now(),document_id))
        db.execute("UPDATE document_versions SET status='cancelled' WHERE document_id=? AND status IN ('pending','processing')",(document_id,))
        paths=[document["file_path"]]
        paths.extend(r[0] for r in db.execute("SELECT markdown_path FROM document_versions WHERE document_id=? AND markdown_path IS NOT NULL",(document_id,)))
        paths.extend(r[0] for r in db.execute("SELECT json_path FROM document_versions WHERE document_id=? AND json_path IS NOT NULL",(document_id,)))
        paths.extend(r[0] for r in db.execute("SELECT file_path FROM document_assets WHERE document_version_id IN (SELECT id FROM document_versions WHERE document_id=?)",(document_id,)))
    try:
        from app.clients.elasticsearch import create_client
        client=create_client()
        # Clean every managed physical index as old mappings may survive behind aliases.
        indices=client.indices.get(index="rag_chunks_v*")
        for index_name in indices:
            client.delete_by_query(index=index_name,query={"term":{"document_id":str(document_id)}},conflicts="proceed",refresh=True,wait_for_completion=True)
        for file_path in set(paths): Path(file_path).unlink(missing_ok=True)
        with connect() as db:
            db.execute("DELETE FROM documents WHERE id=? AND status='deleting'",(document_id,))
    except Exception as exc:
        raise HTTPException(503,"DOCUMENT_CLEANUP_PENDING") from exc
    return ok({"document_uid":document_uid,"deleted":True})


@app.get("/api/v1/documents/{document_uid}")
def get_document(document_uid: str):
    with connect() as db:
        row = db.execute("SELECT * FROM documents WHERE document_uid=?", (document_uid,)).fetchone()
        if not row:
            raise HTTPException(404, "DOCUMENT_NOT_FOUND")
        versions = db.execute("SELECT id,version_number,status,created_at,started_at,completed_at FROM document_versions WHERE document_id=? ORDER BY version_number DESC", (row["id"],)).fetchall()
        tasks = db.execute("SELECT task_uid,status,retry_count,error_type,last_error,created_at,started_at,completed_at FROM parse_tasks WHERE document_id=? ORDER BY created_at DESC", (row["id"],)).fetchall()
    data = dict(row); data.pop("file_path", None)
    return ok({"document": data, "versions": [dict(r) for r in versions], "tasks": [dict(r) for r in tasks]})


@app.get("/api/v1/knowledge/stats")
def knowledge_stats():
    with connect() as db:
        row = db.execute("SELECT count(*) total, coalesce(sum(status='completed'),0) completed, coalesce(sum(status IN ('pending','processing')),0) processing, coalesce(sum(status='failed'),0) failed, coalesce(sum(file_size),0) bytes FROM documents WHERE status<>'deleting'").fetchone()
    return ok(dict(row))


@app.delete("/api/v1/knowledge")
def clear_knowledge():
    settings=get_settings(); paths=[]
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        paths += [r[0] for r in db.execute("SELECT file_path FROM documents")]
        paths += [r[0] for r in db.execute("SELECT file_path FROM document_assets")]
        db.execute("UPDATE parse_tasks SET status='cancelled',execution_stage='cancelled',lease_owner=NULL,lease_token=NULL,lease_until=NULL WHERE status IN ('pending','processing')")
        db.execute("DELETE FROM task_outbox")
        db.execute("DELETE FROM documents")
        db.execute("INSERT INTO operation_logs(log_uid,level,category,action,resource_type,status,message,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid('log'),'WARNING','document','clear_knowledge','knowledge','success','Knowledge base cleared',now()))
    if settings.adapter_mode == 'real':
        try:
            from app.clients.elasticsearch import create_client
            client=create_client()
            if client.indices.exists(index=settings.elasticsearch_index): client.delete_by_query(index=settings.elasticsearch_index,query={"match_all":{}},conflicts="proceed",refresh=True)
        except Exception as exc: raise HTTPException(503,"ELASTICSEARCH_CLEANUP_FAILED") from exc
    for path in paths: Path(path).unlink(missing_ok=True)
    return ok({"cleared":True})


@app.get("/api/v1/conversations/search")
def search_conversations(q: str, limit: int = 50):
    needle=f"%{q}%"
    with connect() as db:
        rows=db.execute("""SELECT c.conversation_uid,c.title,m.message_uid,m.role,substr(m.content,1,240) snippet
          FROM conversations c LEFT JOIN messages m ON m.conversation_id=c.id
          WHERE c.title LIKE ? OR m.content LIKE ? ORDER BY c.is_pinned DESC,c.updated_at DESC,m.id DESC LIMIT ?""",(needle,needle,max(1,min(limit,100)))).fetchall()
    return ok({"items":[dict(r) for r in rows]})


@app.delete("/api/v1/conversations")
def clear_conversations():
    with connect() as db:
        files=[r[0] for r in db.execute("SELECT file_path FROM message_attachments")]
        db.execute("DELETE FROM conversations")
        db.execute("INSERT INTO operation_logs(log_uid,level,category,action,resource_type,status,message,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid('log'),'WARNING','conversation','clear_conversations','conversation','success','All conversations cleared',now()))
    for path in files: Path(path).unlink(missing_ok=True)
    return ok({"cleared":True})


@app.get("/api/v1/conversations/{conversation_uid}/export.md")
def export_conversation(conversation_uid: str):
    with connect() as db:
        conv=db.execute("SELECT id,title FROM conversations WHERE conversation_uid=?",(conversation_uid,)).fetchone()
        if not conv: raise HTTPException(404,"CONVERSATION_NOT_FOUND")
        messages=db.execute("SELECT role,content FROM messages WHERE conversation_id=? ORDER BY id",(conv['id'],)).fetchall()
        sources=db.execute("SELECT document_name,page_number,content_snippet FROM message_sources s JOIN messages m ON m.id=s.message_id WHERE m.conversation_id=? ORDER BY m.id,s.source_order",(conv['id'],)).fetchall()
    body=f"# {conv['title'] or '对话'}\n\n"+"\n\n".join(("## 用户\n\n" if m['role']=='user' else "## 助手\n\n")+m['content'] for m in messages)
    if sources: body+="\n\n## 来源\n\n"+"\n".join(f"- {s['document_name']}"+(f" · 第 {s['page_number']} 页" if s['page_number'] else "")+f": {s['content_snippet']}" for s in sources)
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(body,media_type="text/markdown; charset=utf-8",headers={"Content-Disposition":"attachment; filename=conversation.md"})


@app.get("/api/v1/stats/usage")
def usage_stats():
    with connect() as db:
        missing = db.execute(
            """SELECT r.id, r.conversation_id, r.message_id, m.content AS assistant_content
               FROM llm_call_records r
               JOIN messages m ON m.id = r.message_id
               WHERE r.status = 'success' AND r.total_tokens IS NULL"""
        ).fetchall()
        for row in missing:
            user_row = db.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' AND id<? ORDER BY id DESC LIMIT 1",
                (row["conversation_id"], row["message_id"]),
            ).fetchone()
            snippets = [
                s[0]
                for s in db.execute(
                    "SELECT content_snippet FROM message_sources WHERE message_id=?",
                    (row["message_id"],),
                ).fetchall()
            ]
            prompt_text = " ".join(filter(None, [user_row["content"] if user_row else "", *snippets]))
            in_tok = max(32, estimate_text_tokens(prompt_text) + 48)
            out_tok = estimate_text_tokens(row["assistant_content"] or "")
            db.execute(
                "UPDATE llm_call_records SET input_tokens=?, output_tokens=?, total_tokens=? WHERE id=?",
                (in_tok, out_tok, in_tok + out_tok, row["id"]),
            )
        asks = db.execute("SELECT count(*) FROM messages WHERE role='user'").fetchone()[0]
        answers = db.execute("SELECT count(*) FROM messages WHERE role='assistant' AND status='completed'").fetchone()[0]
        calls = db.execute(
            "SELECT coalesce(sum(total_tokens),0) tokens, coalesce(sum(input_tokens),0) input_tokens, coalesce(sum(output_tokens),0) output_tokens, coalesce(sum(status='failed'),0) failures FROM llm_call_records"
        ).fetchone()
    return ok({
        "questions": asks,
        "answers": answers,
        "tokens": calls["tokens"],
        "input_tokens": calls["input_tokens"],
        "output_tokens": calls["output_tokens"],
        "llm_failures": calls["failures"] or 0,
    })


@app.get("/api/v1/logs")
def list_logs(page:int=1,page_size:int=50):
    with connect() as db:
        db.execute("DELETE FROM operation_logs WHERE created_at < datetime('now','-90 days')")
        rows=db.execute("SELECT log_uid,level,category,action,resource_type,resource_id,status,message,created_at FROM operation_logs ORDER BY id DESC LIMIT ? OFFSET ?",(min(page_size,100),(page-1)*page_size)).fetchall()
    return ok({"items":[dict(r) for r in rows]})


@app.get("/api/v1/settings")
def get_user_settings():
    with connect() as db: row=db.execute("SELECT setting_value FROM user_settings WHERE setting_key='preferences'").fetchone()
    return ok(json.loads(row[0]) if row else {"style":"concise","depth":"standard","language":"zh-CN","instruction":""})


@app.put("/api/v1/settings")
def put_user_settings(body:dict):
    value=json.dumps(body,ensure_ascii=False); stamp=now()
    with connect() as db:
        db.execute("INSERT INTO user_settings(setting_key,setting_value,created_at,updated_at) VALUES('preferences',?,?,?) ON CONFLICT(setting_key) DO UPDATE SET setting_value=excluded.setting_value,updated_at=excluded.updated_at",(value,stamp,stamp))
        db.execute("INSERT INTO operation_logs(log_uid,level,category,action,resource_type,status,message,created_at) VALUES(?,?,?,?,?,?,?,?)",(uid('log'),'INFO','system','settings_updated','settings','success','Preferences updated',stamp))
    return ok(body)


@app.put("/api/v1/messages/{message_uid}/feedback")
def set_message_feedback(message_uid:str, body:dict):
    rating=body.get('rating'); reason=(body.get('reason') or '')[:500]
    if rating not in ('up','down',None): raise HTTPException(422,'INVALID_RATING')
    with connect() as db:
        row=db.execute("SELECT id FROM messages WHERE message_uid=? AND role='assistant'",(message_uid,)).fetchone()
        if not row: raise HTTPException(404,'MESSAGE_NOT_FOUND')
        if rating is None: db.execute("DELETE FROM message_feedback WHERE message_id=?",(row['id'],))
        else: db.execute("INSERT INTO message_feedback(message_id,rating,reason,updated_at) VALUES(?,?,?,?) ON CONFLICT(message_id) DO UPDATE SET rating=excluded.rating,reason=excluded.reason,updated_at=excluded.updated_at",(row['id'],rating,reason,now()))
    return ok({"message_uid":message_uid,"rating":rating,"reason":reason if rating else None})


@app.patch("/api/v1/conversations/{conversation_uid}/preferences")
def patch_conversation_preferences(conversation_uid:str, body:dict):
    with connect() as db:
        cur=db.execute("UPDATE conversations SET preferences_json=?,updated_at=? WHERE conversation_uid=?",(json.dumps(body,ensure_ascii=False),now(),conversation_uid))
        if not cur.rowcount: raise HTTPException(404,'CONVERSATION_NOT_FOUND')
    return ok(body)


class ConversationInput(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationPatch(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    is_pinned: bool | None = None


class MessagePatch(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class RetrievalInput(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    limit: int = Field(default=5, ge=1, le=20)


@app.post("/api/v1/retrieval/search")
def retrieve(body: RetrievalInput):
    if get_settings().adapter_mode != "real":
        raise HTTPException(503, "RETRIEVAL_REQUIRES_REAL_ADAPTERS")
    try:
        return ok({"items": hybrid_search(body.query, body.limit)})
    except Exception as exc:
        raise HTTPException(503, "RETRIEVAL_UNAVAILABLE") from exc


@app.post("/api/v1/conversations")
def create_conversation(body: ConversationInput):
    timestamp, conversation_uid = now(), uid("conv")
    with connect() as db:
        db.execute("INSERT INTO conversations(conversation_uid,title,created_at,updated_at) VALUES(?,?,?,?)", (conversation_uid, body.title, timestamp, timestamp))
    return ok({"conversation_uid": conversation_uid, "title": body.title, "created_at": timestamp, "updated_at": timestamp}, 201)


@app.get("/api/v1/conversations")
def list_conversations(page: int = 1, page_size: int = 20):
    if page < 1 or not 1 <= page_size <= 100: raise HTTPException(422, "PAGINATION_INVALID")
    with connect() as db:
        rows = db.execute("SELECT conversation_uid,title,is_pinned,created_at,updated_at FROM conversations ORDER BY is_pinned DESC,updated_at DESC LIMIT ? OFFSET ?", (page_size,(page-1)*page_size)).fetchall()
    return ok({"items": [dict(r) for r in rows], "page": page, "page_size": page_size})


@app.patch("/api/v1/conversations/{conversation_uid}")
def patch_conversation(conversation_uid: str, body: ConversationPatch):
    if body.title is None and body.is_pinned is None:
        raise HTTPException(422,"PATCH_FIELDS_REQUIRED")
    with connect() as db:
        row=db.execute("SELECT id FROM conversations WHERE conversation_uid=?",(conversation_uid,)).fetchone()
        if not row: raise HTTPException(404,"CONVERSATION_NOT_FOUND")
        fields=[]; values=[]
        if body.title is not None:
            fields.extend(["title=?","is_title_manual=1"]); values.append(body.title)
        if body.is_pinned is not None:
            fields.append("is_pinned=?"); values.append(int(body.is_pinned))
        values.extend([now(),row["id"]])
        db.execute(f"UPDATE conversations SET {','.join(fields)},updated_at=? WHERE id=?",values)
        result=db.execute("SELECT conversation_uid,title,is_pinned,updated_at FROM conversations WHERE id=?",(row["id"],)).fetchone()
    return ok(dict(result))


@app.delete("/api/v1/conversations/{conversation_uid}")
def delete_conversation(conversation_uid: str):
    with connect() as db:
        row=db.execute("SELECT id FROM conversations WHERE conversation_uid=?",(conversation_uid,)).fetchone()
        if not row: raise HTTPException(404,"CONVERSATION_NOT_FOUND")
        files=[r[0] for r in db.execute("SELECT a.file_path FROM message_attachments a JOIN messages m ON m.id=a.message_id WHERE m.conversation_id=?",(row["id"],))]
        db.execute("DELETE FROM conversations WHERE id=?",(row["id"],))
    # File cleanup follows the DB commit; missing files are already considered clean.
    for file_path in files: Path(file_path).unlink(missing_ok=True)
    return ok({"conversation_uid":conversation_uid,"deleted":True})


@app.get("/api/v1/conversations/{conversation_uid}/messages")
def list_messages(conversation_uid: str):
    with connect() as db:
        conv = db.execute("SELECT id FROM conversations WHERE conversation_uid=?", (conversation_uid,)).fetchone()
        if not conv: raise HTTPException(404, "CONVERSATION_NOT_FOUND")
        rows = db.execute("SELECT id,message_uid,role,content,status,error_message,created_at,updated_at FROM messages WHERE conversation_id=? ORDER BY created_at,id", (conv["id"],)).fetchall()
        items=[]
        for row in rows:
            item=dict(row)
            item["attachments"]=[{"attachment_uid":a["attachment_uid"],"filename":a["filename"],"mime_type":a["mime_type"],"url":f"/api/v1/messages/{row['message_uid']}/attachments/{a['attachment_uid']}"} for a in db.execute("SELECT attachment_uid,filename,mime_type FROM message_attachments WHERE message_id=? ORDER BY sort_order",(row["id"],))]
            item["sources"]=[dict(s) for s in db.execute("""SELECT s.document_name,s.page_number,s.chunk_id,s.content_snippet,s.asset_id,s.locations_json,
                d.document_uid FROM message_sources s LEFT JOIN documents d ON d.id=s.document_id
                WHERE s.message_id=? ORDER BY s.source_order""",(row["id"],))]
            for source in item["sources"]:
                source["locations"]=json.loads(source.pop("locations_json") or "[]")
                source["image_url"]=(f"/api/v1/documents/{source['document_uid']}/assets/{source['asset_id']}" if source.get("document_uid") and source.get("asset_id") else None)
            item.pop("id",None)
            items.append(item)
    return ok({"items": items})


@app.get("/api/v1/documents/{document_uid}/file")
def get_document_file(document_uid: str):
    with connect() as db:
        row=db.execute("SELECT filename,file_path FROM documents WHERE document_uid=?",(document_uid,)).fetchone()
    if not row or not Path(row["file_path"]).is_file(): raise HTTPException(404,"DOCUMENT_FILE_NOT_FOUND")
    return FileResponse(row["file_path"],media_type="application/pdf",filename=row["filename"],content_disposition_type="inline")


@app.get("/api/v1/messages/{message_uid}/attachments/{attachment_uid}")
def get_message_attachment(message_uid: str, attachment_uid: str):
    with connect() as db:
        row=db.execute("SELECT a.file_path,a.mime_type FROM message_attachments a JOIN messages m ON m.id=a.message_id WHERE m.message_uid=? AND a.attachment_uid=?",(message_uid,attachment_uid)).fetchone()
    if not row or not Path(row["file_path"]).is_file(): raise HTTPException(404,"ATTACHMENT_NOT_FOUND")
    return FileResponse(row["file_path"],media_type=row["mime_type"],filename=Path(row["file_path"]).name)


@app.patch("/api/v1/messages/{message_uid}")
def edit_user_message(message_uid: str, body: MessagePatch):
    timestamp=now()
    with connect() as db:
        message=db.execute("SELECT id,conversation_id,role,created_at FROM messages WHERE message_uid=?",(message_uid,)).fetchone()
        if not message: raise HTTPException(404,"MESSAGE_NOT_FOUND")
        if message["role"]!="user": raise HTTPException(409,"ONLY_USER_MESSAGES_CAN_BE_EDITED")
        following=db.execute("SELECT id FROM messages WHERE conversation_id=? AND (created_at>? OR (created_at=? AND id>?))",
                             (message["conversation_id"],message["created_at"],message["created_at"],message["id"])).fetchall()
        ids=[r["id"] for r in following]
        paths=[]
        if ids:
            marks=','.join('?' for _ in ids)
            paths=[r[0] for r in db.execute(f"SELECT file_path FROM message_attachments WHERE message_id IN ({marks})",ids)]
            db.execute(f"DELETE FROM messages WHERE id IN ({marks})",ids)
        db.execute("UPDATE messages SET content=?,updated_at=? WHERE id=?",(body.content.strip(),timestamp,message["id"]))
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?",(timestamp,message["conversation_id"]))
    for path in paths: Path(path).unlink(missing_ok=True)
    return ok({"message_uid":message_uid,"content":body.content.strip(),"updated_at":timestamp})


@app.delete("/api/v1/messages/{message_uid}")
def delete_message(message_uid: str):
    with connect() as db:
        message=db.execute("SELECT id,conversation_id FROM messages WHERE message_uid=?",(message_uid,)).fetchone()
        if not message: raise HTTPException(404,"MESSAGE_NOT_FOUND")
        paths=[r[0] for r in db.execute("SELECT file_path FROM message_attachments WHERE message_id=?",(message["id"],))]
        db.execute("DELETE FROM messages WHERE id=?",(message["id"],))
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?",(now(),message["conversation_id"]))
    for path in paths: Path(path).unlink(missing_ok=True)
    return ok({"message_uid":message_uid,"deleted":True})


@app.post("/api/v1/messages/{message_uid}/retry")
def retry_assistant_message(message_uid: str):
    timestamp=now(); operation_uid=uid("op")
    with connect() as db:
        assistant=db.execute("SELECT id,conversation_id,created_at FROM messages WHERE message_uid=? AND role='assistant'",(message_uid,)).fetchone()
        if not assistant: raise HTTPException(404,"ASSISTANT_MESSAGE_NOT_FOUND")
        previous=db.execute("SELECT id,message_uid,content FROM messages WHERE conversation_id=? AND role='user' AND (created_at<? OR (created_at=? AND id<?)) ORDER BY created_at DESC,id DESC LIMIT 1",
                            (assistant["conversation_id"],assistant["created_at"],assistant["created_at"],assistant["id"])).fetchone()
        if not previous: raise HTTPException(409,"USER_MESSAGE_NOT_FOUND")
        attachments=db.execute("SELECT file_path,mime_type FROM message_attachments WHERE message_id=? ORDER BY sort_order",(previous["id"],)).fetchall()
        db.execute("DELETE FROM message_sources WHERE message_id=?",(assistant["id"],))
        db.execute("UPDATE messages SET content='',status='pending',error_message=NULL,updated_at=? WHERE id=?",(timestamp,assistant["id"]))
        conversation_id=assistant["conversation_id"]; assistant_id=assistant["id"]; question=previous["content"]

    async def events():
        answer=[]; started=time.monotonic()
        try:
            with connect() as db:
                history=db.execute("SELECT id,role,content FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 10",(conversation_id,previous["id"])).fetchall()
            history=list(reversed(history)); sources=hybrid_search(question,5) if get_settings().adapter_mode=="real" else []
            context="\n\n".join(f"[{i+1}] {item.get('content','')}" for i,item in enumerate(sources))
            prompt=[{"role":"system","content":"Answer using the supplied knowledge context. Cite source numbers such as [1]. If context is empty, say you do not have retrieved supporting material."}]
            prompt.extend({"role":row["role"],"content":row["content"]} for row in history)
            content=[{"type":"text","text":f"Question:\n{question}\n\nRetrieved context:\n{context}"}]
            content.extend({"type":"image_url","image_url":{"url":"data:"+a["mime_type"]+";base64,"+base64.b64encode(Path(a["file_path"]).read_bytes()).decode("ascii"),"detail":"auto"}} for a in attachments)
            asset_ids=list(dict.fromkeys(asset_id for source in sources for asset_id in source.get("image_asset_ids",[])))[:3]
            visual_assets=[]
            if asset_ids:
                marks=','.join('?' for _ in asset_ids)
                with connect() as db:
                    visual_assets=db.execute(f"SELECT file_path,mime_type FROM document_assets WHERE id IN ({marks})",[int(x) for x in asset_ids]).fetchall()
                content.extend({"type":"image_url","image_url":{"url":"data:"+asset["mime_type"]+";base64,"+base64.b64encode(Path(asset["file_path"]).read_bytes()).decode("ascii"),"detail":"auto"}} for asset in visual_assets)
            prompt.append({"role":"user","content":content if (attachments or visual_assets) else content[0]["text"]})
            with connect() as db: db.execute("UPDATE messages SET status='streaming',updated_at=? WHERE id=?",(now(),assistant_id))
            yield sse("message_start",{"message_id":message_uid})
            usage: dict[str, int] = {}
            async for delta in stream_answer(prompt, usage): answer.append(delta); yield sse("content_delta",{"delta":delta})
            timestamp=now()
            with connect() as db:
                db.execute("UPDATE messages SET content=?,status='completed',updated_at=? WHERE id=?",("".join(answer),timestamp,assistant_id))
                order=0
                for item in sources:
                    for asset_id in (item.get("image_asset_ids") or [None]):
                        db.execute("INSERT INTO message_sources(message_id,document_id,document_version_id,chunk_id,asset_id,document_name,page_number,content_snippet,source_order,created_at,locations_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (assistant_id,int(item["document_id"]),int(item["document_version_id"]),item["chunk_id"],int(asset_id) if asset_id else None,item.get("document_name",str(item["document_id"])),item.get("page_start"),item.get("content","")[:500],order,timestamp,json.dumps(item.get("locations",[]),ensure_ascii=False)))
                        order+=1
                db.execute("INSERT INTO llm_call_records(call_uid,operation_uid,conversation_id,message_id,operation_type,provider,model_name,attempt_number,status,input_tokens,output_tokens,total_tokens,latency_ms,started_at,completed_at,created_at) VALUES(?,?,?,?,?,'deepseek',?,1,'success',?,?,?,?,?,?,?)",
                           (uid("call"),operation_uid,conversation_id,assistant_id,"response_generation",get_settings().deepseek_model,usage.get("input_tokens"),usage.get("output_tokens"),usage.get("total_tokens"),int((time.monotonic()-started)*1000),timestamp,timestamp,timestamp))
            yield sse("sources",{"items":[{"document_id":source["document_id"],"document_uid":source.get("document_uid"),"document_name":source.get("document_name"),"document_version_id":source["document_version_id"],"chunk_id":source["chunk_id"],"page_number":source.get("page_start"),"content_snippet":source.get("content","")[:500],"locations":source.get("locations",[]),"images":[{"asset_id":asset,"url":f"/api/v1/documents/{source.get('document_uid')}/assets/{asset}"} for asset in source.get("image_asset_ids",[])]} for source in sources]})
            yield sse("message_complete",{"message_id":message_uid,"finish_reason":"stop"})
        except Exception:
            timestamp=now()
            with connect() as db:
                db.execute("UPDATE messages SET content=?,status='failed',error_message='Answer generation failed',updated_at=? WHERE id=?",("".join(answer),timestamp,assistant_id))
                db.execute("INSERT INTO llm_call_records(call_uid,operation_uid,conversation_id,message_id,operation_type,provider,model_name,attempt_number,status,error_type,error_message,started_at,completed_at,created_at) VALUES(?,?,?,?,?,'deepseek',?,1,'failed','UpstreamError','Answer generation failed',?,?,?)",
                           (uid("call"),operation_uid,conversation_id,assistant_id,"response_generation",get_settings().deepseek_model,timestamp,timestamp,timestamp))
            yield sse("error",{"code":"LLM_REQUEST_FAILED","message":"模型服务暂时不可用"})
    return StreamingResponse(events(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/api/v1/conversations/{conversation_uid}/messages")
async def send_message(conversation_uid: str, content: Annotated[str, Form(min_length=1, max_length=12000)],
                       images: Annotated[list[UploadFile] | None, File()] = None,
                       document_uids: Annotated[str | None, Form()] = None,
                       style: Annotated[str | None, Form()] = None,
                       depth: Annotated[str | None, Form()] = None,
                       instruction: Annotated[str | None, Form()] = None):
    question = content.strip()
    if not question:
        raise HTTPException(422, "MESSAGE_REQUIRED")
    images=images or []
    if len(images)>5:
        raise HTTPException(422,"TOO_MANY_IMAGES")
    timestamp = now()
    user_uid, assistant_uid, operation_uid = uid("msg"), uid("msg"), uid("op")
    with connect() as db:
        conversation = db.execute("SELECT id,preferences_json,title,is_title_manual FROM conversations WHERE conversation_uid=?", (conversation_uid,)).fetchone()
        if not conversation:
            raise HTTPException(404, "CONVERSATION_NOT_FOUND")
        conversation_id = conversation["id"]
        if not conversation["is_title_manual"] and conversation["title"] in (None, "", "新对话"):
            title = re.sub(r"\s+", " ", question).strip()
            if len(title) > 32:
                title = title[:32].rstrip() + "…"
            db.execute("UPDATE conversations SET title=? WHERE id=?", (title, conversation_id))
        conv_preferences=json.loads(conversation['preferences_json']) if conversation['preferences_json'] else {}
        db.execute("INSERT INTO messages(message_uid,conversation_id,role,content,status,created_at,updated_at) VALUES(?,?, 'user',?,'completed',?,?)",
                   (user_uid,conversation_id,question,timestamp,timestamp))
        user_id = db.execute("SELECT id FROM messages WHERE message_uid=?", (user_uid,)).fetchone()["id"]
        stored=[]
        for order,image in enumerate(images):
            raw_name=Path(image.filename or "image").name
            name=re.sub(r"[^A-Za-z0-9._-]","_",raw_name)[:120] or "image"
            declared=image.content_type or ""
            if declared not in ("image/jpeg","image/png","image/webp"):
                raise HTTPException(422,"UNSUPPORTED_IMAGE_TYPE")
            payload=await image.read(10*1024*1024+1)
            if not payload or len(payload)>10*1024*1024:
                raise HTTPException(413,"IMAGE_TOO_LARGE")
            # Validate the byte signature instead of trusting the multipart MIME header.
            actual=("image/jpeg" if payload.startswith(b"\xff\xd8\xff") else
                    "image/png" if payload.startswith(b"\x89PNG\r\n\x1a\n") else
                    "image/webp" if len(payload)>=12 and payload[:4]==b"RIFF" and payload[8:12]==b"WEBP" else None)
            if actual != declared:
                raise HTTPException(422,"IMAGE_CONTENT_TYPE_MISMATCH")
            stored.append((name,declared,payload,order))
            await image.close()
        db.execute("INSERT INTO messages(message_uid,conversation_id,role,content,status,created_at,updated_at) VALUES(?,?, 'assistant','', 'pending',?,?)",
                   (assistant_uid,conversation_id,timestamp,timestamp))
        assistant_id = db.execute("SELECT id FROM messages WHERE message_uid=?", (assistant_uid,)).fetchone()["id"]
        for name,mime,payload,order in stored:
            attachment_uid=uid("att")
            destination=get_settings().storage_path/"conversations"/conversation_uid/"messages"/user_uid/f"{attachment_uid}_{name}"
            destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(payload)
            db.execute("INSERT INTO message_attachments(attachment_uid,message_id,filename,file_path,mime_type,file_size,sort_order,created_at) VALUES(?,?,?,?,?,?,?,?)",
                       (attachment_uid,user_id,name,str(destination),mime,len(payload),order,timestamp))
        db.execute("UPDATE conversations SET updated_at=? WHERE id=?",(timestamp,conversation_id))

    async def event_stream():
        answer = []
        started = time.monotonic()
        try:
            yield sse("message_start",{"message_id":assistant_uid,"user_message_id":user_uid})
            yield sse("progress",{"stage":"retrieval","message":"正在检索知识库…"})
            with connect() as db:
                history = db.execute("SELECT id,role,content FROM messages WHERE conversation_id=? AND id<? ORDER BY id DESC LIMIT 10",
                                     (conversation_id,user_id)).fetchall()
                history=list(reversed(history))
                history_attachments={r["id"]:db.execute("SELECT file_path,mime_type FROM message_attachments WHERE message_id=? ORDER BY sort_order",(r["id"],)).fetchall() for r in history if r["role"]=="user"}
            selected_docs=[x for x in (document_uids or '').split(',') if x.strip()]
            # Lightweight rewrite: use the current question and recent user turn for underspecified follow-ups.
            prior_question=next((r['content'] for r in reversed(history) if r['role']=='user'), '')
            retrieval_query=(prior_question+' '+question).strip() if prior_question and len(question)<24 else question
            sources = hybrid_search(retrieval_query,5,selected_docs or None) if get_settings().adapter_mode == "real" else []
            context = "\n\n".join(f"[{i+1}] {item.get('content','')}" for i,item in enumerate(sources))
            with connect() as db: global_row=db.execute("SELECT setting_value FROM user_settings WHERE setting_key='preferences'").fetchone()
            preferences=json.loads(global_row[0]) if global_row else {}
            preferences.update(conv_preferences)
            preferences.update({k:v for k,v in {'style':style,'depth':depth,'instruction':instruction}.items() if v is not None})
            system="Answer using the supplied knowledge context. Cite source numbers such as [1]. If context is empty, say you do not have retrieved supporting material."
            system+=f"\nAnswer style: {preferences.get('style','concise')}; depth: {preferences.get('depth','standard')}; language: {preferences.get('language','zh-CN')}."
            if preferences.get('instruction'): system+="\nUser preference: "+str(preferences['instruction'])[:1000]
            prompt = [{"role":"system","content":system}]
            def image_parts(rows):
                return [{"type":"image_url","image_url":{"url":"data:"+row["mime_type"]+";base64,"+base64.b64encode(Path(row["file_path"]).read_bytes()).decode("ascii"),"detail":"auto"}} for row in rows]
            for row in history:
                if row["role"]=="user" and history_attachments.get(row["id"]):
                    prompt.append({"role":"user","content":[{"type":"text","text":row["content"]},*image_parts(history_attachments[row["id"]])]})
                else:
                    prompt.append({"role":row["role"],"content":row["content"]})
            user_content=[{"type":"text","text":f"Question:\n{question}\n\nRetrieved context:\n{context}"}]
            # Read persisted current-message attachments from SQLite for the multimodal request.
            with connect() as db:
                current_attachments=db.execute("SELECT file_path,mime_type FROM message_attachments WHERE message_id=? ORDER BY sort_order",(user_id,)).fetchall()
            user_content.extend(image_parts(current_attachments))
            # Include a small number of images attached to retrieved PDF chunks as visual evidence.
            source_assets=[]
            if sources:
                asset_ids=list(dict.fromkeys(asset_id for source in sources for asset_id in source.get("image_asset_ids",[])))[:3]
                if asset_ids:
                    marks=','.join('?' for _ in asset_ids)
                    with connect() as db:
                        source_assets=db.execute(f"SELECT id,file_path,mime_type FROM document_assets WHERE id IN ({marks})",[int(x) for x in asset_ids]).fetchall()
            user_content.extend(image_parts(source_assets))
            prompt.append({"role":"user","content":user_content if (current_attachments or source_assets) else user_content[0]["text"]})
            with connect() as db:
                db.execute("UPDATE messages SET status='streaming',updated_at=? WHERE id=?",(now(),assistant_id))
            yield sse("progress",{"stage":"generation","message":"正在生成回答…"})
            usage: dict[str, int] = {}
            async for delta in stream_answer(prompt, usage):
                answer.append(delta)
                yield sse("content_delta",{"delta":delta})
            timestamp = now()
            with connect() as db:
                db.execute("UPDATE messages SET content=?,status='completed',updated_at=? WHERE id=?",("".join(answer),timestamp,assistant_id))
                order=0
                for item in sources:
                    asset_ids=item.get("image_asset_ids") or [None]
                    for asset_id in asset_ids:
                        db.execute("INSERT INTO message_sources(message_id,document_id,document_version_id,chunk_id,asset_id,document_name,page_number,content_snippet,source_order,created_at,locations_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                   (assistant_id,int(item["document_id"]),int(item["document_version_id"]),item["chunk_id"],
                                    int(asset_id) if asset_id else None,item.get("document_name",str(item["document_id"])),item.get("page_start"),item.get("content","")[:500],order,timestamp,json.dumps(item.get("locations",[]),ensure_ascii=False)))
                        order+=1
                db.execute("INSERT INTO llm_call_records(call_uid,operation_uid,conversation_id,message_id,operation_type,provider,model_name,attempt_number,status,input_tokens,output_tokens,total_tokens,latency_ms,started_at,completed_at,created_at) VALUES(?,?,?,?,?,'deepseek',?,1,'success',?,?,?,?,?,?,?)",
                           (uid("call"),operation_uid,conversation_id,assistant_id,"response_generation",get_settings().deepseek_model,
                            usage.get("input_tokens"),usage.get("output_tokens"),usage.get("total_tokens"),int((time.monotonic()-started)*1000),timestamp,timestamp,timestamp))
            yield sse("sources",{"items":[{"document_id":s["document_id"],"document_uid":s.get("document_uid"),"document_name":s.get("document_name"),"document_version_id":s["document_version_id"],"chunk_id":s["chunk_id"],"page_number":s.get("page_start"),"content_snippet":s.get("content","")[:500],"locations":s.get("locations",[]),"images":[{"asset_id":asset_id,"url":f"/api/v1/documents/{s.get('document_uid')}/assets/{asset_id}"} for asset_id in s.get("image_asset_ids",[])]} for s in sources]})
            yield sse("message_complete",{"message_id":assistant_uid,"finish_reason":"stop"})
        except Exception:
            timestamp=now()
            with connect() as db:
                db.execute("UPDATE messages SET content=?,status='failed',error_message='Answer generation failed',updated_at=? WHERE id=?",("".join(answer),timestamp,assistant_id))
                db.execute("INSERT INTO llm_call_records(call_uid,operation_uid,conversation_id,message_id,operation_type,provider,model_name,attempt_number,status,error_type,error_message,started_at,completed_at,created_at) VALUES(?,?,?,?,?,'deepseek',?,1,'failed','UpstreamError','Answer generation failed',?,?,?)",
                           (uid("call"),operation_uid,conversation_id,assistant_id,"response_generation",get_settings().deepseek_model,timestamp,timestamp,timestamp))
            yield sse("error",{"code":"LLM_REQUEST_FAILED","message":"模型服务暂时不可用"})

    return StreamingResponse(event_stream(),media_type="text/event-stream",headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

