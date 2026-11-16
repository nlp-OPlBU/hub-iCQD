"""文档接口：上传、去重、查询、重投（真实 MySQL / Kafka，不 mock）。"""

from __future__ import annotations

import shutil
import time
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

pytestmark = pytest.mark.integration


def make_pdf(text: str = "多模态 RAG 演示文档") -> bytes:
    try:
        import pymupdf as fitz
    except ImportError:  # pragma: no cover
        import fitz  # type: ignore

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), text)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture()
def clean_storage(mysql_schema) -> None:
    from sqlalchemy import text

    from app.core.config import get_settings

    settings = get_settings()
    with mysql_schema.begin() as conn:
        conn.execute(text("DELETE FROM chunk"))
        conn.execute(text("DELETE FROM document"))
    for path in (settings.storage.raw, settings.storage.parsed):
        shutil.rmtree(path, ignore_errors=True)
        path.mkdir(parents=True, exist_ok=True)


@pytest.fixture()
async def client():
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_upload_creates_pending_document(client: AsyncClient, clean_storage) -> None:
    pdf = make_pdf()
    response = await client.post(
        "/api/v1/documents",
        files={"file": ("demo.pdf", pdf, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["created"] is True
    doc_id = body["document"]["id"]
    assert body["document"]["status"] == "pending"
    assert body["document"]["size_bytes"] == len(pdf)

    detail = await client.get(f"/api/v1/documents/{doc_id}")
    assert detail.status_code == 200
    assert detail.json()["filename"] == "demo.pdf"

    listing = await client.get("/api/v1/documents", params={"limit": 10})
    assert listing.status_code == 200
    assert listing.json()["total"] >= 1
    assert any(item["id"] == doc_id for item in listing.json()["items"])


async def test_duplicate_upload_returns_existing(client: AsyncClient, clean_storage) -> None:
    pdf = make_pdf("同一份文件")
    first = await client.post("/api/v1/documents", files={"file": ("a.pdf", pdf, "application/pdf")})
    second = await client.post("/api/v1/documents", files={"file": ("b.pdf", pdf, "application/pdf")})
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["document"]["id"] == first.json()["document"]["id"]


async def test_reject_unsupported_suffix(client: AsyncClient, clean_storage) -> None:
    response = await client.post(
        "/api/v1/documents",
        files={"file": ("note.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_unknown_document_returns_404(client: AsyncClient, clean_storage) -> None:
    response = await client.get(f"/api/v1/documents/{uuid.uuid4().hex}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


async def test_retry_requires_failed_state(client: AsyncClient, clean_storage) -> None:
    pdf = make_pdf("待重试文档")
    uploaded = await client.post("/api/v1/documents", files={"file": ("r.pdf", pdf, "application/pdf")})
    doc_id = uploaded.json()["document"]["id"]

    response = await client.post(f"/api/v1/documents/{doc_id}/retry")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT_STATE"


async def test_upload_enqueues_kafka_message(client: AsyncClient, clean_storage, kafka_topics) -> None:
    from app.core.config import get_settings
    from app.infra.mq.kafka_client import create_consumer

    pdf = make_pdf("入队验证")
    uploaded = await client.post("/api/v1/documents", files={"file": ("q.pdf", pdf, "application/pdf")})
    doc_id = uploaded.json()["document"]["id"]

    consumer = create_consumer(topics=[get_settings().kafka.topic], group_id=f"test-{uuid.uuid4().hex[:8]}")
    message = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline and message is None:
            for records in consumer.poll(timeout_ms=1000).values():
                for record in records:
                    if record.value.get("doc_id") == doc_id:
                        message = record.value
                        break
    finally:
        consumer.close()

    assert message is not None
    assert Path(message["path"]).exists()
    assert message["trace_id"]


async def test_upload_exposes_chunks_after_ingest(client: AsyncClient, clean_storage, monkeypatch) -> None:
    """上传后跑一次摄取（解析器用本地桩），接口能读到分块。"""
    from app.pipeline import ingest, mineru

    def fake_parse(doc_id: str, pdf_path: Path, output_dir: Path) -> mineru.ParseResult:
        markdown = output_dir / "auto" / "demo.md"
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text("# 标题\n\n" + "多模态检索内容。" * 60, encoding="utf-8")
        return mineru.ParseResult(output_dir=output_dir, markdown_path=markdown, page_count=1)

    monkeypatch.setattr(mineru, "parse_document", fake_parse)

    pdf = make_pdf("分块验证")
    uploaded = await client.post("/api/v1/documents", files={"file": ("c.pdf", pdf, "application/pdf")})
    doc_id = uploaded.json()["document"]["id"]

    ingest.process_document(doc_id)

    detail = (await client.get(f"/api/v1/documents/{doc_id}")).json()
    assert detail["status"] == "done"
    assert detail["chunk_count"] > 0

    chunks = (await client.get(f"/api/v1/documents/{doc_id}/chunks")).json()
    assert len(chunks) == detail["chunk_count"]
    assert chunks[0]["idx"] == 0
