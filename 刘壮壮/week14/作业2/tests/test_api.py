from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


def test_health_and_request_id():
    with TestClient(app) as client:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "req-test"})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == "req-test"
    assert response.json()["data"]["database"] == "ok"


def test_upload_creates_document_and_outbox():
    with TestClient(app) as client:
        response = client.post("/api/v1/documents", files={"files": ("a.pdf", b"%PDF-1.7 demo", "application/pdf")})
        listing = client.get("/api/v1/documents")
    assert response.status_code == 201
    item = response.json()["data"]["items"][0]
    assert item["status"] == "pending"
    assert listing.json()["data"]["total"] == 1


def test_upload_rejects_non_pdf():
    with TestClient(app) as client:
        response = client.post("/api/v1/documents", files={"files": ("a.txt", b"hello", "text/plain")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "PDF_REQUIRED"


def test_idempotency_replays_upload_response():
    headers={"Idempotency-Key":"upload-replay-test"}
    with TestClient(app) as client:
        first=client.post("/api/v1/documents",files={"files":("idem.pdf",b"%PDF-1.7 demo","application/pdf")},headers=headers)
        second=client.post("/api/v1/documents",files={"files":("idem.pdf",b"%PDF-1.7 demo","application/pdf")},headers=headers)
    assert first.status_code==201
    assert second.status_code==201
    assert second.json()==first.json()


def test_conversation_creation_and_message_listing():
    with TestClient(app) as client:
        created = client.post("/api/v1/conversations", json={"title": "Research"})
        uid = created.json()["data"]["conversation_uid"]
        messages = client.get(f"/api/v1/conversations/{uid}/messages")
    assert created.status_code == 201
    assert messages.status_code == 200
    assert messages.json()["data"]["items"] == []


def test_local_message_stream_persists_assistant_answer():
    with TestClient(app) as client:
        created = client.post("/api/v1/conversations", json={"title": "Local Q&A"})
        uid = created.json()["data"]["conversation_uid"]
        response = client.post(f"/api/v1/conversations/{uid}/messages", data={"content": "What is RAG?"})
        messages = client.get(f"/api/v1/conversations/{uid}/messages").json()["data"]["items"]
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: message_start" in response.text
    assert "event: content_delta" in response.text
    assert "event: message_complete" in response.text
    assert messages[-1]["status"] == "completed"


def test_reparse_creates_new_version_and_outbox():
    from app.db import connect
    with TestClient(app) as client:
        uploaded=client.post("/api/v1/documents",files={"files":("reparse.pdf",b"%PDF-1.7", "application/pdf")}).json()["data"]["items"][0]
        with connect() as db:
            doc=db.execute("SELECT id,current_version_id FROM documents WHERE document_uid=?",(uploaded["document_uid"],)).fetchone()
            db.execute("UPDATE documents SET status='completed' WHERE id=?",(doc["id"],))
            db.execute("UPDATE document_versions SET status='completed' WHERE id=?",(doc["current_version_id"],))
            db.execute("UPDATE parse_tasks SET status='completed',execution_stage='completed' WHERE document_id=?",(doc["id"],))
        response=client.post(f"/api/v1/documents/{uploaded['document_uid']}/reparse")
        tasks=client.get(f"/api/v1/documents/{uploaded['document_uid']}/tasks")
    assert response.status_code == 202
    assert response.json()["data"]["version_number"] == 2
    assert tasks.json()["data"]["total"] == 2


def test_delete_marks_then_cleans_document(monkeypatch):
    class Indices:
        def get(self, **kwargs): return {"rag_chunks_v1": {}}
    class Client:
        indices=Indices()
        def delete_by_query(self, **kwargs): return {"deleted":1}
    monkeypatch.setattr("app.clients.elasticsearch.create_client",lambda:Client())
    with TestClient(app) as client:
        uploaded=client.post("/api/v1/documents",files={"files":("delete.pdf",b"%PDF-1.7", "application/pdf")}).json()["data"]["items"][0]
        response=client.delete(f"/api/v1/documents/{uploaded['document_uid']}")
        listing=client.get("/api/v1/documents")
    assert response.status_code == 200
    assert listing.json()["data"]["total"] == 0


def test_conversation_update_and_delete():
    with TestClient(app) as client:
        uid=client.post("/api/v1/conversations",json={"title":"Old"}).json()["data"]["conversation_uid"]
        patched=client.patch(f"/api/v1/conversations/{uid}",json={"title":"New","is_pinned":True})
        assert patched.json()["data"]["title"]=="New"
        assert patched.json()["data"]["is_pinned"]==1
        deleted=client.delete(f"/api/v1/conversations/{uid}")
        missing=client.get(f"/api/v1/conversations/{uid}/messages")
    assert deleted.status_code==200
    assert missing.status_code==404


def test_edit_user_message_deletes_later_messages():
    with TestClient(app) as client:
        conv=client.post("/api/v1/conversations",json={}).json()["data"]["conversation_uid"]
        client.post(f"/api/v1/conversations/{conv}/messages",data={"content":"first"})
        messages=client.get(f"/api/v1/conversations/{conv}/messages").json()["data"]["items"]
        first_user=messages[0]["message_uid"]
        edited=client.patch(f"/api/v1/messages/{first_user}",json={"content":"revised"})
        remaining=client.get(f"/api/v1/conversations/{conv}/messages").json()["data"]["items"]
    assert edited.status_code==200
    assert len(remaining)==1 and remaining[0]["content"]=="revised"


def test_message_accepts_image_attachment_and_cleans_it():
    from app.db import connect
    png=b"\x89PNG\r\n\x1a\n"+b"test-image-bytes"
    with TestClient(app) as client:
        conv=client.post("/api/v1/conversations",json={}).json()["data"]["conversation_uid"]
        response=client.post(f"/api/v1/conversations/{conv}/messages",data={"content":"Describe this"},
                             files={"images":("tiny.png",png,"image/png")})
        with connect() as db:
            row=db.execute("SELECT file_path FROM message_attachments").fetchone()
        image_path=Path(row["file_path"])
        deleted=client.delete(f"/api/v1/conversations/{conv}")
    assert response.status_code==200
    assert "event: message_complete" in response.text
    assert not image_path.exists()
    assert deleted.status_code==200


def test_retry_reuses_assistant_message_without_duplicating_user():
    from app.db import connect
    with TestClient(app) as client:
        conv=client.post("/api/v1/conversations",json={}).json()["data"]["conversation_uid"]
        client.post(f"/api/v1/conversations/{conv}/messages",data={"content":"retry me"})
        before=client.get(f"/api/v1/conversations/{conv}/messages").json()["data"]["items"]
        assistant=before[-1]["message_uid"]
        response=client.post(f"/api/v1/messages/{assistant}/retry")
        after=client.get(f"/api/v1/conversations/{conv}/messages").json()["data"]["items"]
        with connect() as db:
            calls=db.execute("SELECT count(*) FROM llm_call_records WHERE message_id=(SELECT id FROM messages WHERE message_uid=?)",(assistant,)).fetchone()[0]
    assert response.status_code==200
    assert "event: message_complete" in response.text
    assert len(after)==2 and after[-1]["message_uid"]==assistant
    assert after[0]["content"]=="retry me"
    assert calls==2


def test_usage_and_knowledge_stats_track_tokens_and_documents():
    with TestClient(app) as client:
        k_empty = client.get("/api/v1/knowledge/stats").json()["data"]
        assert k_empty == {"total": 0, "completed": 0, "processing": 0, "failed": 0, "bytes": 0}

        conv = client.post("/api/v1/conversations", json={"title": "Stats Q&A"}).json()["data"]["conversation_uid"]
        client.post(f"/api/v1/conversations/{conv}/messages", data={"content": "请总结核心架构设计"})
        stats = client.get("/api/v1/stats/usage").json()["data"]

    assert stats["questions"] == 1
    assert stats["answers"] == 1
    assert stats["tokens"] > 0
    assert stats["input_tokens"] > 0
    assert stats["output_tokens"] > 0
    assert stats["tokens"] == stats["input_tokens"] + stats["output_tokens"]
