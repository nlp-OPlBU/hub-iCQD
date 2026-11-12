"""真实中间件连通性与最小往返（不使用 mock）。"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


def test_mysql_schema_roundtrip(mysql_schema) -> None:
    from app.infra.db.engine import session_scope
    from app.infra.db.models import Document

    doc_id = uuid.uuid4().hex
    sha256 = (uuid.uuid4().hex + uuid.uuid4().hex)[:64]
    with session_scope() as session:
        session.execute(text("DELETE FROM document"))
        session.add(Document(id=doc_id, filename="demo.pdf", sha256=sha256, size_bytes=1024))

    with session_scope() as session:
        row = session.get(Document, doc_id)
        assert row is not None
        assert row.status == "pending"
        assert row.progress == 0
        assert row.chunk_count == 0


def test_redis_projection(redis_clean) -> None:
    redis_clean.hset("doc:test", mapping={"status": "parsing", "progress": 10})
    assert redis_clean.hget("doc:test", "status") == "parsing"
    redis_clean.expire("doc:test", 60)
    assert redis_clean.ttl("doc:test") > 0


def test_trace_span_persisted_to_mysql_and_redis(mysql_schema, redis_clean) -> None:
    from app.core.tracing import TraceSession, span
    from app.infra.cache.redis_client import read_trace_spans
    from app.infra.db.engine import get_engine

    session = TraceSession()
    with session:
        with span("chunk", ref_id="doc-test"):
            pass
        with span("embed_text"):
            pass
    session.flush()

    cached = read_trace_spans(session.trace_id)
    assert [item["span"] for item in cached] == ["chunk", "embed_text"]

    with get_engine().connect() as conn:
        count = conn.execute(
            text("SELECT COUNT(*) FROM trace_span WHERE trace_id = :tid"), {"tid": session.trace_id}
        ).scalar_one()
    assert count == 2


def test_kafka_produce_consume(kafka_topics: dict[str, str]) -> None:
    from app.core.config import get_settings
    from app.infra.mq.kafka_client import create_consumer, publish

    settings = get_settings()
    doc_id = f"smoke-{uuid.uuid4().hex[:8]}"
    payload = {"v": 1, "doc_id": doc_id, "filename": "a.pdf"}

    publish(settings.kafka.topic, doc_id, payload)

    consumer = create_consumer(topics=[settings.kafka.topic], group_id=f"test-{uuid.uuid4().hex[:8]}")
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

    assert received == payload


def test_milvus_collections_and_search(milvus_ready: list[str]) -> None:
    from app.core.config import get_settings
    from app.infra.vector.milvus_client import get_milvus

    settings = get_settings().milvus
    client = get_milvus()
    doc_id = f"vec-{uuid.uuid4().hex[:8]}"
    dim = settings.text_dim

    client.delete(collection_name=settings.text_collection, filter=f'doc_id == "{doc_id}"')
    client.insert(
        collection_name=settings.text_collection,
        data=[
            {
                "chunk_id": f"{doc_id}-0",
                "doc_id": doc_id,
                "kind": "paragraph",
                "page_no": 1,
                "text": "多模态检索测试片段",
                "embedding": [0.01] * dim,
            }
        ],
    )

    hits = client.search(
        collection_name=settings.text_collection,
        data=[[0.01] * dim],
        filter=f'doc_id == "{doc_id}"',
        limit=1,
        output_fields=["doc_id"],
    )
    flat = [hit for group in hits for hit in group]
    assert flat
    entity = flat[0].get("entity") or flat[0]
    assert entity.get("doc_id") == doc_id
