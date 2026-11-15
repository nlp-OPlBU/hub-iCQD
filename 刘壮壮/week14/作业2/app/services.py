"""Replaceable integration boundary; local mode is deterministic for development."""
import json
import sqlite3
import uuid
from datetime import datetime, timezone

from app.config import get_settings
from app.db import connect


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def enqueue_parse_task(document_id: int, version_id: int, task_uid: str) -> None:
    """Persist an Outbox event atomically; a dispatcher can safely publish it later."""
    settings = get_settings()
    with connect() as db:
        task = db.execute("SELECT id FROM parse_tasks WHERE task_uid=?", (task_uid,)).fetchone()
        if not task:
            raise RuntimeError("parse task missing")
        event_uid = uid("evt")
        payload = json.dumps({"event_uid": event_uid, "event_type": "document.parse.requested",
                              "schema_version": 1, "task_uid": task_uid, "created_at": now()})
        db.execute("INSERT INTO task_outbox(event_uid,task_id,event_type,topic,payload,status,created_at,updated_at) VALUES(?,?,?,?,?,'pending',?,?)",
                   (event_uid, task["id"], "document.parse.requested", settings.kafka_topic, payload, now(), now()))


def adapter_status() -> dict:
    return {"mode": get_settings().adapter_mode, "kafka": "outbox_ready", "mineru": "not_configured",
            "elasticsearch": "not_configured", "llm": "not_configured"}
