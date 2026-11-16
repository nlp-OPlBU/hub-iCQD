import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from .config import DATA

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db():
    conn = sqlite3.connect(DATA / 'rag.db', timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

def init():
    with db() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, sha256 TEXT NOT NULL,
          size INTEGER NOT NULL, pages INTEGER NOT NULL,
          status TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          error TEXT, parser TEXT, chunk_count INTEGER DEFAULT 0, image_count INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS chunks (
          id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, page INTEGER, kind TEXT,
          text TEXT NOT NULL, image_url TEXT);
        CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
        CREATE TABLE IF NOT EXISTS outbox (
          event_id TEXT PRIMARY KEY, doc_id TEXT NOT NULL, payload TEXT NOT NULL,
          sent_at TEXT, last_error TEXT);
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, event TEXT,
          detail TEXT, created_at TEXT NOT NULL);
        ''')

def log(c, doc_id, event, detail=''):
    c.execute('INSERT INTO events(doc_id,event,detail,created_at) VALUES(?,?,?,?)',
              (doc_id, event, detail, now()))

def enqueue(c, doc_id, attempt):
    payload = {'schema': 1, 'document_id': doc_id, 'attempt': attempt,
               'event_id': f'{doc_id}:{attempt}', 'created_at': now()}
    c.execute('INSERT INTO outbox(event_id,doc_id,payload) VALUES(?,?,?)',
              (payload['event_id'], doc_id, json.dumps(payload)))
    log(c, doc_id, 'queued', f'attempt={attempt}')

def documents():
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM documents ORDER BY created_at DESC')]

def document(doc_id):
    with db() as c:
        r = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
        return dict(r) if r else None
