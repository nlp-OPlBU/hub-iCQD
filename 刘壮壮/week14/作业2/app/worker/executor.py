"""Recoverable local parser worker. It deliberately leaves indexing-stage jobs processing until ES indexing is available."""
import asyncio
import io
import json
import logging
import mimetypes
import re
import uuid
import zipfile
from pathlib import Path, PurePosixPath

import httpx

from app.clients.mineru import MinerUClient
from app.config import get_settings
from app.db import connect
from app.services import now

logger = logging.getLogger(__name__)


def claim_task(worker_id: str, task_uid: str | None = None):
    timestamp = now()
    token = uuid.uuid4().hex
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        task_filter = " AND t.task_uid=?" if task_uid else ""
        params = (timestamp, task_uid) if task_uid else (timestamp,)
        row = db.execute(f"""SELECT t.id,t.task_uid,t.document_id,t.document_version_id,d.file_path,d.document_uid,v.mineru_batch_id,v.markdown_path,t.execution_stage
            FROM parse_tasks t JOIN documents d ON d.id=t.document_id
            JOIN document_versions v ON v.id=t.document_version_id
            WHERE t.status='pending' AND d.status IN ('pending','processing')
            AND t.execution_stage IN ('queued','submitting','polling','indexing')
            AND (t.next_retry_at IS NULL OR t.next_retry_at<=?) {task_filter}
            ORDER BY t.created_at,t.id LIMIT 1""", params).fetchone()
        if not row:
            return None
        stage = row["execution_stage"] or ("polling" if row["mineru_batch_id"] else "submitting")
        updated = db.execute("""UPDATE parse_tasks SET status='processing',execution_stage=?,lease_owner=?,lease_token=?,lease_until=?,
            started_at=COALESCE(started_at,?),stage_started_at=?,updated_at=? WHERE id=? AND status='pending'""",
            (stage,worker_id,token,
             __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z"),
             timestamp,timestamp,timestamp,row["id"])).rowcount
        if not updated:
            return None
        db.execute("UPDATE documents SET status='processing',updated_at=? WHERE id=? AND status='pending'",(timestamp,row["document_id"]))
        db.execute("UPDATE document_versions SET status='processing',started_at=COALESCE(started_at,?) WHERE id=?",(timestamp,row["document_version_id"]))
        result = dict(row); result["lease_token"] = token
        return result


def heartbeat(task, worker_id):
    from datetime import datetime, timedelta, timezone
    until = (datetime.now(timezone.utc)+timedelta(minutes=5)).isoformat(timespec="seconds").replace("+00:00","Z")
    with connect() as db:
        return db.execute("UPDATE parse_tasks SET lease_until=?,updated_at=? WHERE id=? AND lease_owner=? AND lease_token=? AND status='processing'",
                          (until,now(),task["id"],worker_id,task["lease_token"])).rowcount == 1


def lease_is_active(task, worker_id):
    with connect() as db:
        return db.execute("""SELECT 1 FROM parse_tasks t JOIN documents d ON d.id=t.document_id
            WHERE t.id=? AND t.status='processing' AND t.lease_owner=? AND t.lease_token=? AND d.status!='deleting'""",
            (task["id"],worker_id,task["lease_token"])).fetchone() is not None


def save_batch(task, worker_id, batch_id):
    with connect() as db:
        changed = db.execute("UPDATE document_versions SET mineru_batch_id=? WHERE id=? AND EXISTS(SELECT 1 FROM parse_tasks WHERE id=? AND lease_owner=? AND lease_token=? AND status='processing')",
                             (batch_id,task["document_version_id"],task["id"],worker_id,task["lease_token"])).rowcount
        if not changed:
            raise RuntimeError("Task lease lost before MinerU batch id persistence")
        db.execute("UPDATE parse_tasks SET execution_stage='polling',stage_started_at=?,updated_at=? WHERE id=?",(now(),now(),task["id"]))


async def download_and_save(task, result):
    settings = get_settings()
    root = settings.storage_path / task["document_uid"] / f"version-{task['document_version_id']}"
    root.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
        response = await client.get(result["full_zip_url"])
        response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        for info in archive.infolist():
            member = PurePosixPath(info.filename)
            if member.is_absolute() or ".." in member.parts:
                raise RuntimeError("Unsafe path in MinerU archive")
            if info.is_dir():
                continue
            target = root.joinpath(*member.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
    # Register extracted images in SQLite so retrieval can resolve multimodal assets.
    image_rows=[]
    for image_path in root.rglob("*"):
        if not image_path.is_file() or image_path.suffix.lower() not in {".jpg",".jpeg",".png",".webp"}:
            continue
        page_match=re.search(r"(?:page|p)[_-]?(\d+)",image_path.stem,re.IGNORECASE)
        image_rows.append((image_path,page_match.group(1) if page_match else None))
    with connect() as db:
        existing={r[0] for r in db.execute("SELECT file_path FROM document_assets WHERE document_version_id=?",(task["document_version_id"],))}
        for image_path,page in image_rows:
            if str(image_path) in existing: continue
            mime=mimetypes.guess_type(image_path.name)[0]
            if mime not in {"image/jpeg","image/png","image/webp"}: continue
            db.execute("INSERT INTO document_assets(document_version_id,asset_type,filename,file_path,page_number,mime_type,file_size,created_at) VALUES(?,'image',?,?,?,?,?,?)",
                       (task["document_version_id"],image_path.name,str(image_path),int(page) if page else None,mime,image_path.stat().st_size,now()))
    markdown = next(root.rglob("*.md"), None)
    if not markdown:
        raise RuntimeError("MinerU archive did not contain Markdown output")
    layout=next(root.rglob("layout.json"),None)
    if not layout:
        layout=next(root.rglob("*content_list*.json"),None)
    with connect() as db:
        db.execute("UPDATE document_versions SET markdown_path=?,json_path=? WHERE id=?",
                   (str(markdown),str(layout) if layout else None,task["document_version_id"]))
    return markdown.read_text(encoding="utf-8",errors="replace")


async def process(task, worker_id):
    try:
        stage = task.get("execution_stage") or "queued"
        if stage != "indexing":
            client = MinerUClient()
            path = Path(task["file_path"])
            batch_id = task.get("mineru_batch_id")
            if not batch_id:
                batch_id = await client.submit_local_pdf(path, task["task_uid"])
                save_batch(task,worker_id,batch_id)
            result = await client.wait_for_result(batch_id,path.name)
            heartbeat(task,worker_id)
            markdown = await download_and_save(task,result)
            with connect() as db:
                db.execute("UPDATE parse_tasks SET execution_stage='indexing',last_error=NULL,updated_at=? WHERE id=? AND lease_owner=? AND lease_token=?",
                           (now(),task["id"],worker_id,task["lease_token"]))
        else:
            markdown = Path(task["markdown_path"]).read_text(encoding="utf-8",errors="replace")

        from app.worker.indexing import index_markdown
        if not lease_is_active(task,worker_id):
            return
        with connect() as db:
            layout_path=db.execute("SELECT json_path FROM document_versions WHERE id=?",(task["document_version_id"],)).fetchone()[0]
        indexed_count = await asyncio.to_thread(index_markdown, task["document_id"], task["document_version_id"], markdown, layout_path)
        timestamp = now()
        # Publish the new active version only after all chunks are refreshed and visible in ES.
        with connect() as db:
            owns_lease = db.execute("SELECT 1 FROM parse_tasks WHERE id=? AND status='processing' AND lease_owner=? AND lease_token=?",
                                    (task["id"],worker_id,task["lease_token"])).fetchone()
            doc = db.execute("SELECT status,current_version_id FROM documents WHERE id=?",(task["document_id"],)).fetchone()
            if not owns_lease or doc["status"] == "deleting":
                # Deletion may have raced with the ES bulk request; remove this version again.
                from app.clients.elasticsearch import create_client
                es=create_client()
                managed=es.indices.get(index="rag_chunks_v*")
                for index_name in managed:
                    es.delete_by_query(index=index_name,query={"term":{"document_version_id":str(task["document_version_id"])}},conflicts="proceed",refresh=True)
                return
            db.execute("UPDATE document_versions SET status='completed',completed_at=? WHERE id=?",(timestamp,task["document_version_id"]))
            db.execute("UPDATE documents SET status='completed',current_version_id=?,updated_at=? WHERE id=?",(task["document_version_id"],timestamp,task["document_id"]))
            db.execute("UPDATE parse_tasks SET status='completed',execution_stage='completed',completed_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=?",
                       (timestamp,timestamp,task["id"]))
        print(f"Indexed {task['task_uid']}: {indexed_count} chunks")
    except Exception as exc:
        logger.exception("Document parse task %s failed", task["task_uid"])
        with connect() as db:
            db.execute("UPDATE parse_tasks SET status='failed',execution_stage='failed',error_type=?,last_error=?,completed_at=?,lease_owner=NULL,lease_token=NULL,lease_until=NULL,updated_at=? WHERE id=? AND lease_owner=? AND lease_token=?",
                       (type(exc).__name__,str(exc)[:1000],now(),now(),task["id"],worker_id,task["lease_token"]))
            db.execute("UPDATE document_versions SET status='failed',completed_at=? WHERE id=?",(now(),task["document_version_id"]))
            old_version=db.execute("SELECT current_version_id FROM documents WHERE id=?",(task["document_id"],)).fetchone()
            fallback="completed" if old_version and old_version["current_version_id"] and old_version["current_version_id"] != task["document_version_id"] else "failed"
            db.execute("UPDATE documents SET status=?,updated_at=? WHERE id=? AND status!='deleting'",(fallback,now(),task["document_id"]))


async def run_worker():
    settings=get_settings()
    if settings.adapter_mode == "real":
        from app.worker.kafka_consumer import run_consumer
        await run_consumer()
        return
    active=set()
    while True:
        active={t for t in active if not t.done()}
        while len(active)<settings.worker_concurrency:
            task=claim_task(settings.worker_id)
            if not task: break
            active.add(asyncio.create_task(process(task,settings.worker_id)))
        await asyncio.sleep(1)


def main():
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
