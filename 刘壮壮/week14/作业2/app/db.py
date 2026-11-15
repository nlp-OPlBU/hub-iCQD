import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(settings.database_path, timeout=5, isolation_level="DEFERRED")
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    db.execute("PRAGMA busy_timeout = 5000")
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    settings = get_settings()
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    schema = Path(__file__).resolve().parents[1] / "doc" / "multimodal_rag_schema.sql"
    with sqlite3.connect(settings.database_path) as db:
        db.execute("PRAGMA journal_mode = WAL")
        db.execute("PRAGMA foreign_keys = ON")
        db.executescript(schema.read_text(encoding="utf-8"))
        # Additive migration for databases created before the worker runtime fields existed.
        columns = {row[1] for row in db.execute("PRAGMA table_info(parse_tasks)")}
        additions = {
            "execution_stage": "TEXT NOT NULL DEFAULT 'queued'",
            "lease_owner": "TEXT",
            "lease_token": "TEXT",
            "lease_until": "TEXT",
            "stage_started_at": "TEXT",
        }
        for name, declaration in additions.items():
            if name not in columns:
                db.execute(f"ALTER TABLE parse_tasks ADD COLUMN {name} {declaration}")
        conversation_columns = {row[1] for row in db.execute("PRAGMA table_info(conversations)")}
        if "preferences_json" not in conversation_columns:
            db.execute("ALTER TABLE conversations ADD COLUMN preferences_json TEXT")
        source_columns={row[1] for row in db.execute("PRAGMA table_info(message_sources)")}
        if "locations_json" not in source_columns:
            db.execute("ALTER TABLE message_sources ADD COLUMN locations_json TEXT")
        db.execute("CREATE TABLE IF NOT EXISTS message_feedback (message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE, rating TEXT NOT NULL CHECK(rating IN ('up','down')), reason TEXT, updated_at TEXT NOT NULL)")
        db.execute("CREATE TABLE IF NOT EXISTS idempotency_keys (key TEXT NOT NULL, route TEXT NOT NULL, created_at TEXT NOT NULL, response_status INTEGER, response_body BLOB, content_type TEXT, PRIMARY KEY(key,route))")
        idem_columns={row[1] for row in db.execute("PRAGMA table_info(idempotency_keys)")}
        for name,declaration in {"response_status":"INTEGER","response_body":"BLOB","content_type":"TEXT"}.items():
            if name not in idem_columns: db.execute(f"ALTER TABLE idempotency_keys ADD COLUMN {name} {declaration}")
