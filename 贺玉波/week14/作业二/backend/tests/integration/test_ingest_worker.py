"""worker 消息处理：成功落库、失败重试上限转 DLQ、崩溃后从头重跑。"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pytest

from app.pipeline.mineru import ParseError

pytestmark = pytest.mark.integration


@pytest.fixture()
def clean_storage(mysql_schema) -> None:
    from sqlalchemy import text

    from app.core.config import get_settings

    settings = get_settings()
    with mysql_schema.begin() as conn:
        conn.execute(text("DELETE FROM chunk"))
        conn.execute(text("DELETE FROM document"))
    for path in (settings.storage.raw, settings.storage.parsed):
        path.mkdir(parents=True, exist_ok=True)
        for item in path.glob("*"):
            if item.is_file():
                item.unlink()


def seed_document() -> tuple[str, dict]:
    from app.core.config import get_settings
    from app.infra.db import repo_document as repo
    from app.infra.db.engine import session_scope

    settings = get_settings()
    doc_id = uuid.uuid4().hex
    raw_path = settings.storage.raw / f"{doc_id}.pdf"
    raw_path.write_bytes(b"%PDF-1.4 stub")
    sha256 = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    with session_scope() as session:
        repo.create_document(
            session, doc_id=doc_id, filename="demo.pdf", sha256=sha256, size_bytes=raw_path.stat().st_size
        )
    payload = {"v": 1, "doc_id": doc_id, "path": str(raw_path), "filename": "demo.pdf", "sha256": sha256}
    return doc_id, payload


def fake_parse(markdown_text: str):
    from app.pipeline import mineru

    def _parse(doc_id: str, pdf_path: Path, output_dir: Path) -> mineru.ParseResult:
        markdown = output_dir / "auto" / "demo.md"
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(markdown_text, encoding="utf-8")
        return mineru.ParseResult(output_dir=output_dir, markdown_path=markdown, page_count=2)

    return _parse


def test_success_marks_done(clean_storage, monkeypatch) -> None:
    from app.infra.db import repo_document as repo
    from app.infra.db.engine import session_scope
    from app.pipeline import mineru
    from app.worker import ingest_worker

    monkeypatch.setattr(mineru, "parse_document", fake_parse("# 第一章\n\n" + "内容。" * 120))
    doc_id, payload = seed_document()

    assert ingest_worker.process_message(payload) is False

    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        assert doc is not None
        assert doc.status == "done"
        assert doc.progress == 100
        assert doc.chunk_count > 0
        assert doc.page_count == 2
        assert doc.md_path and doc.md_path.endswith(".md")
        assert repo.count_chunks(session, doc_id) == doc.chunk_count


def test_failure_goes_to_dlq_after_limit(clean_storage, kafka_topics, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.infra.db import repo_document as repo
    from app.infra.db.engine import session_scope
    from app.infra.mq.kafka_client import create_consumer
    from app.pipeline import mineru
    from app.worker import ingest_worker

    def boom(doc_id: str, pdf_path: Path, output_dir: Path):
        raise ParseError("模拟解析崩溃")

    monkeypatch.setattr(mineru, "parse_document", boom)
    monkeypatch.setattr(get_settings().mineru, "max_attempts", 1)

    doc_id, payload = seed_document()
    assert ingest_worker.process_message(payload) is False

    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        assert doc is not None
        assert doc.status == "failed"
        assert doc.error_code == "PARSE_FAILED"
        assert doc.attempts == 1

    consumer = create_consumer(
        topics=[get_settings().kafka.dlq_topic], group_id=f"test-{uuid.uuid4().hex[:8]}"
    )
    received = None
    try:
        deadline = time.time() + 30
        while time.time() < deadline and received is None:
            for records in consumer.poll(timeout_ms=1000).values():
                for record in records:
                    if record.value.get("doc_id") == doc_id:
                        received = record.value
                        break
    finally:
        consumer.close()

    assert received is not None
    assert "模拟解析崩溃" in received["error"]


def test_retry_returns_true_and_bumps_attempts(clean_storage, monkeypatch) -> None:
    from app.core.config import get_settings
    from app.infra.db import repo_document as repo
    from app.infra.db.engine import session_scope
    from app.pipeline import mineru
    from app.worker import ingest_worker

    def boom(doc_id: str, pdf_path: Path, output_dir: Path):
        raise ParseError("第一次失败")

    monkeypatch.setattr(mineru, "parse_document", boom)
    monkeypatch.setattr(get_settings().mineru, "max_attempts", 3)
    monkeypatch.setattr(ingest_worker, "BACKOFF_BASE_S", 0.0)

    doc_id, payload = seed_document()
    assert ingest_worker.process_message(payload) is True

    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        assert doc is not None
        assert doc.attempts == 1
        assert doc.status != "done"


def test_crash_recovery_restarts_from_pending(clean_storage, monkeypatch) -> None:
    from app.infra.db import repo_document as repo
    from app.infra.db.engine import session_scope
    from app.pipeline import mineru
    from app.worker import ingest_worker

    monkeypatch.setattr(mineru, "parse_document", fake_parse("# 恢复\n\n" + "崩溃前的半成品。" * 40))
    doc_id, payload = seed_document()

    with session_scope() as session:
        repo.update_document(session, doc_id, status="parsing", stage="parsing", progress=10)

    assert ingest_worker.process_message(payload) is False

    with session_scope() as session:
        doc = repo.get_document(session, doc_id)
        assert doc is not None
        assert doc.status == "done"
