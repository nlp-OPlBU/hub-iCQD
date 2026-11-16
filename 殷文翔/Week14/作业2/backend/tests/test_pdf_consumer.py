import json
import threading
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.db.base import Base, new_id, utcnow
from app.main import create_app
from app.models import Document, OutboxEvent, ParseTask
from app.repositories.outbox import add_parse_requested
from app.repositories.task import (TaskClaim, claim_task, complete_task, fail_task,
                                   schedule_retry)
from app.schemas.document import ParseEvent
from app.services.task_processing import TaskProcessingError, TaskResult
from app.workers import pdf_consumer
from app.workers.outbox_dispatcher import OutboxDispatcher
from app.workers.pdf_consumer import PdfWorker


class FakeProcessor:
    def __init__(self):
        self.calls = 0
        self.error: Exception | None = None

    def process(self, claim):
        self.calls += 1
        if self.error:
            raise self.error
        return TaskResult(artifact_key=f"parsed/{claim.document_id}/v{claim.version}",
                          index_name="test-index", indexed_at=utcnow(),
                          retrieval_mode=claim.retrieval_mode)


class FakePublisher:
    def __init__(self):
        self.calls = []

    def publish(self, topic, key, payload, timeout=None):
        self.calls.append((topic, key, payload))

    def close(self):
        pass


class FakeConsumer:
    def __init__(self, records, on_commit=None):
        self.records = list(records)
        self.committed = 0
        self.rewound = 0
        self.closed = False
        self.on_commit = on_commit

    def poll(self, timeout):
        return self.records.pop(0) if self.records else None

    def commit(self):
        self.committed += 1
        if self.on_commit:
            self.on_commit()

    def rewind(self, record):
        self.rewound += 1
        self.records.append(record)

    def close(self):
        self.closed = True


def record(value):
    return SimpleNamespace(topic="t", partition=0, offset=1, key=None, value=value)


@pytest.fixture(params=['sqlite', 'mysql'])
def worker_env(request, tmp_path, monkeypatch):
    root_engine = None
    if request.param == 'mysql':
        from alembic import command
        from alembic.config import Config
        from app.core.config import PROJECT_ROOT
        from app.db.session import create_db_engine
        root_engine, mysql = request.getfixturevalue('isolated_mysql')
        database = 'rag_worker_' + uuid4().hex
        with root_engine.begin() as connection:
            connection.execute(text(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4'))
        engine = create_db_engine(mysql.model_copy(update={'mysql_database': database}))
        config = Config(str(PROJECT_ROOT / 'backend/alembic.ini'))
        with engine.connect() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.commit()
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}",
                               connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def enable_fks(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data",
                        task_max_retries=3, task_retry_base_seconds=5,
                        task_retry_max_seconds=300, task_lease_seconds=120,
                        task_heartbeat_seconds=30)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("app.main.create_db_engine", lambda _: engine)
    yield engine, factory, settings
    engine.dispose()
    if root_engine is not None:
        with root_engine.begin() as connection:
            connection.execute(text(f'DROP DATABASE `{database}`'))


def test_expired_redelivery_cannot_bypass_retry_budget(worker_env):
    _, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings, max_retries=0)
    now = utcnow()
    with factory() as session, session.begin():
        claim_task(session, task_id=task_id, document_id=document_id, version=1,
                   worker_id='crashed', settings=settings, now=now)
    processor = FakeProcessor()
    worker = make_worker(settings, factory, processor)
    assert worker.handle_message(payload, now=now + timedelta(seconds=121))['outcome'] == 'failed'
    assert worker.handle_message(payload, now=now + timedelta(seconds=122))['outcome'] == 'failed'
    assert processor.calls == 0
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.attempts == 1 and task.error_code == 'WORKER_LOST'
        assert task.finished_at and task.lease_token is None
        assert len(session.scalars(select(OutboxEvent).where(
            OutboxEvent.event_type == 'pdf.parse.failed')).all()) == 1


def seed(factory, settings, *, max_retries=3, status="pending"):
    document_id, task_id = new_id(), new_id()
    with factory() as session, session.begin():
        session.add(Document(id=document_id, original_filename="a.pdf",
                             storage_key=f"uploads/{document_id}.pdf", sha256="a" * 64,
                             size_bytes=10))
        session.flush()
        session.add(ParseTask(id=task_id, document_id=document_id, version=1,
                              status=status, stage="queued", attempts=0,
                              max_retries=max_retries))
        session.flush()
        add_parse_requested(session, settings, ParseEvent(
            event_id=new_id(), document_id=document_id, task_id=task_id,
            version=1, storage_key=f"uploads/{document_id}.pdf"))
    payload = json.dumps({"schema_version": 1, "event_id": str(new_id()),
                          "document_id": document_id, "task_id": task_id,
                          "version": 1, "storage_key": f"uploads/{document_id}.pdf"}).encode()
    return document_id, task_id, payload


def make_worker(settings, factory, processor=None, consumer=None):
    return PdfWorker(settings, factory, consumer or FakeConsumer([]),
                     processor or FakeProcessor(), worker_id="worker-1")


def test_worker_completes_once_and_duplicate_delivery_is_idempotent(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    processor = FakeProcessor()
    worker = make_worker(settings, factory, processor)

    assert worker.handle_message(payload)["outcome"] == "completed"
    assert worker.handle_message(payload)["outcome"] == "completed"
    assert processor.calls == 1
    with factory() as session:
        task = session.get(ParseTask, task_id)
        document = session.get(Document, document_id)
        assert task.status == "completed" and task.stage == "done"
        assert task.attempts == 1 and task.error_code is None
        assert task.artifact_key and task.index_name == "test-index"
        assert document.current_searchable_version == 1


def test_worker_retries_with_backoff_then_fails_to_failure_topic(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings, max_retries=1)
    processor = FakeProcessor()
    processor.error = TaskProcessingError("MINERU_TIMEOUT", "timeout", retryable=True)
    worker = make_worker(settings, factory, processor)
    now = utcnow()

    assert worker.handle_message(payload, now=now)["outcome"] == "retry_scheduled"
    with factory() as session:
        task = session.get(ParseTask, task_id)
        outbox = session.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "pdf.parse.requested"))
        assert task.status == "pending" and task.attempts == 1
        assert task.next_attempt_at > now
        assert task.error_code == "MINERU_TIMEOUT"
        assert outbox.status == "pending" and outbox.available_at == task.next_attempt_at
    # Early redelivery must not jump the persisted retry schedule.
    assert worker.handle_message(payload, now=now + timedelta(seconds=1))["outcome"] == "not_due"
    assert processor.calls == 1
    assert worker.handle_message(payload, now=now + timedelta(seconds=10))["outcome"] == "failed"
    with factory() as session:
        task = session.get(ParseTask, task_id)
        failed = session.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "pdf.parse.failed"))
        assert task.status == "failed" and task.attempts == 2
        assert task.error_code == "MINERU_TIMEOUT"
        assert failed.status == "pending" and failed.topic == settings.kafka_failed_topic


def test_non_retryable_error_fails_immediately(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    processor = FakeProcessor()
    processor.error = TaskProcessingError("INVALID_PDF", "bad pdf", retryable=False)
    worker = make_worker(settings, factory, processor)
    assert worker.handle_message(payload)["outcome"] == "failed"
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.status == "failed" and task.attempts == 1
        assert task.finished_at is not None


def test_active_lease_blocks_duplicate_and_expired_lease_is_recovered(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    now = utcnow()
    with factory() as session, session.begin():
        task = session.get(ParseTask, task_id)
        task.status, task.stage, task.attempts = "processing", "parsing", 1
        task.lease_owner, task.lease_token = "other", new_id()
        task.lease_expires_at = now + timedelta(minutes=5)
    processor = FakeProcessor()
    worker = make_worker(settings, factory, processor)
    assert worker.handle_message(payload, now=now)["outcome"] == "busy"
    assert processor.calls == 0

    publisher = FakePublisher()
    with factory() as session, session.begin():
        task = session.get(ParseTask, task_id)
        task.lease_expires_at = now - timedelta(seconds=1)
    stats = OutboxDispatcher(settings, factory, publisher, owner="d").run_once(now=now)
    assert stats["recovered"] == 1
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.status == "pending" and task.error_code == "WORKER_LOST"
        assert task.attempts == 1
    assert worker.handle_message(payload, now=now)["outcome"] == "completed"
    assert processor.calls == 1


def test_old_worker_cannot_overwrite_new_version(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    with factory() as session, session.begin():
        claim = claim_task(session, task_id=task_id, document_id=document_id, version=1,
                           worker_id="old-worker", settings=settings).claim
        assert claim is not None
        session.add(ParseTask(id=new_id(), document_id=document_id, version=2,
                              status="failed", stage="failed", attempts=0, max_retries=3))
        session.get(Document, document_id).latest_version = 2
    with factory() as session, session.begin():
        assert complete_task(session, claim, artifact_key="parsed/old", index_name="idx",
                             indexed_at=utcnow(), retrieval_mode="bm25") is False
        assert schedule_retry(session, claim, settings=settings, error_code="X",
                              error_message="x", error_details={}) is False
        assert fail_task(session, claim, settings=settings, error_code="X",
                         error_message="x", error_details={}) is False
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.status == "processing"  # Still processing; no overwrite happened.
        assert session.get(Document, document_id).current_searchable_version is None
        assert session.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "pdf.parse.failed")) is None
    assert make_worker(settings, factory).handle_message(payload)["outcome"] == "stale"


def test_offset_commits_only_after_result_is_persisted(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    stop = threading.Event()

    def verify_then_stop():
        with factory() as session:
            assert session.get(ParseTask, task_id).status == "completed"
        stop.set()

    consumer = FakeConsumer([record(payload)], on_commit=verify_then_stop)
    PdfWorker(settings, factory, consumer, FakeProcessor(), worker_id="w").run_forever(stop)
    assert consumer.committed == 1 and consumer.rewound == 0 and consumer.closed


def test_persistence_failure_rewinds_and_does_not_commit(worker_env, monkeypatch):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    stop = threading.Event()
    consumer = FakeConsumer([record(payload)])

    def unavailable(*args, **kwargs):
        raise OperationalError("COMMIT", {}, Exception("db down"))

    monkeypatch.setattr(pdf_consumer, "complete_task", unavailable)
    threading.Timer(0.2, stop.set).start()
    PdfWorker(settings, factory, consumer, FakeProcessor(), worker_id="w").run_forever(stop)
    assert consumer.committed == 0
    assert consumer.rewound >= 1
    with factory() as session:
        assert session.get(ParseTask, task_id).status == "processing"


def test_manual_retry_creates_new_version_and_is_idempotent(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id = new_id(), new_id()
    with factory() as session, session.begin():
        session.add(Document(id=document_id, original_filename="a.pdf",
                             storage_key=f"uploads/{document_id}.pdf", sha256="a" * 64,
                             size_bytes=10))
        session.flush()
        session.add(ParseTask(id=task_id, document_id=document_id, version=1,
                              status="failed", stage="failed", attempts=4, max_retries=3,
                              error_code="PARSE_FAILED", error_message="failed"))
    app = create_app(settings)
    key = str(uuid4())
    with TestClient(app) as client:
        response = client.post(f"/api/documents/{document_id}/retry",
                               json={"expected_latest_version": 1},
                               headers={"Idempotency-Key": key})
        assert response.status_code == 202, response.text
        first = response.json()
        assert first["version"] == 2 and first["status"] == "pending"
        again = client.post(f"/api/documents/{document_id}/retry",
                            json={"expected_latest_version": 1},
                            headers={"Idempotency-Key": key})
        assert again.status_code == 202 and again.json()["id"] == first["id"]
        conflict = client.post(f"/api/documents/{document_id}/retry",
                               json={"expected_latest_version": 1},
                               headers={"Idempotency-Key": str(uuid4())})
        assert conflict.status_code == 409
        assert conflict.json()["error"]["code"] == "VERSION_CONFLICT"
    with factory() as session:
        document = session.get(Document, document_id)
        assert document.latest_version == 2
        event = session.scalar(select(OutboxEvent).where(
            OutboxEvent.task_id == first["id"]))
        assert event.payload["version"] == 2
        assert event.payload["storage_key"] == document.storage_key


def test_expired_lease_takeover_invalidates_old_token(worker_env):
    engine, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings)
    now = utcnow()
    with factory() as session, session.begin():
        old = claim_task(session, task_id=task_id, document_id=document_id, version=1,
                         worker_id="old", settings=settings, now=now).claim
        assert old is not None
        session.get(ParseTask, task_id).lease_expires_at = now - timedelta(seconds=1)
    with factory() as session, session.begin():
        new = claim_task(session, task_id=task_id, document_id=document_id, version=1,
                         worker_id="new", settings=settings,
                         now=now + timedelta(seconds=1)).claim
        assert new is not None and new.lease_token != old.lease_token
    with factory() as session, session.begin():
        assert complete_task(session, old, artifact_key="old", index_name="idx",
                             indexed_at=utcnow(), retrieval_mode="bm25") is False
    with factory() as session, session.begin():
        assert complete_task(session, new, artifact_key="new", index_name="idx",
                             indexed_at=utcnow(), retrieval_mode="bm25") is True
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.status == "completed" and task.artifact_key == "new"
        assert session.get(Document, document_id).current_searchable_version == 1


@pytest.mark.parametrize('failure,code', [
    ('timeout', 'MINERU_TIMEOUT'), ('zip', 'INVALID_ZIP'), ('es', 'INDEX_UNAVAILABLE'),
])
def test_real_processor_failures_are_persisted(worker_env, monkeypatch, failure, code):
    from app.services.pdf_processing import MineruTaskProcessor
    from app.services.mineru_cli import MineruError
    from app.services.elasticsearch_store import ElasticsearchError
    from tests.test_mineru_cli import artifact_zip

    _, factory, settings = worker_env
    document_id, task_id, payload = seed(factory, settings, max_retries=0)
    pdf = settings.data_dir / 'uploads' / f'{document_id}.pdf'
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b'%PDF-test')
    processor = MineruTaskProcessor(settings)

    def run(pdf, output, workspace):
        workspace.mkdir(parents=True)
        if failure == 'timeout':
            raise MineruError('MINERU_TIMEOUT', '解析超时')
        if failure == 'zip':
            output.write_bytes(b'corrupt')
        else:
            artifact_zip(output)
    monkeypatch.setattr(processor.runner, 'run', run)
    def index(chunks):
        raise ElasticsearchError('bulk item failed')
    monkeypatch.setattr(processor.index, 'index_chunks', index)
    assert make_worker(settings, factory, processor).handle_message(payload)['outcome'] == 'failed'
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.error_code == code and task.error_details['stage']
        assert task.status == 'failed' and task.finished_at
        assert session.get(Document, document_id).current_searchable_version is None
        assert session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == 'pdf.parse.failed'))


def test_retrieval_filters_incomplete_versions_and_user_scope(worker_env):
    from app.repositories.document import DocumentRepository
    _, factory, settings = worker_env
    doc, tid, _ = seed(factory, settings)
    other, other_tid, _ = seed(factory, settings)
    with factory() as session, session.begin():
        repository = DocumentRepository(session)
        assert repository.searchable_versions() == {}
        session.get(ParseTask, tid).status = 'completed'
        session.get(Document, doc).current_searchable_version = 1
        session.flush()
        assert repository.searchable_versions() == {doc: 1}
        assert repository.searchable_versions([other]) == {}
        assert repository.searchable_versions([]) == {}
        assert repository.searchable_versions([doc]) == {doc: 1}
