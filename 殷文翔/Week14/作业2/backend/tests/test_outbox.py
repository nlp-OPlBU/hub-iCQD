from datetime import timedelta

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.db.base import Base, new_id, utcnow
from app.models import Document, OutboxEvent, ParseTask
from app.repositories.outbox import add_parse_requested
from app.schemas.document import ParseEvent
from app.services.kafka_producer import PublishError
from app.workers.outbox_dispatcher import OutboxDispatcher


class FakePublisher:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.calls: list[tuple[str, str, dict]] = []
        self.closed = False

    def publish(self, topic, key, payload, timeout=None):
        if self.error:
            raise self.error
        self.calls.append((topic, key, payload))

    def close(self):
        self.closed = True


@pytest.fixture(params=['sqlite', 'mysql'])
def outbox_env(request, tmp_path):
    root_engine = None
    if request.param == 'mysql':
        from alembic import command
        from alembic.config import Config
        from app.core.config import PROJECT_ROOT
        from app.db.session import create_db_engine
        root_engine, mysql = request.getfixturevalue('isolated_mysql')
        database = 'rag_outbox_' + new_id().replace('-', '')
        with root_engine.begin() as connection:
            connection.execute(text(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4'))
        engine = create_db_engine(mysql.model_copy(update={'mysql_database': database}))
        config = Config(str(PROJECT_ROOT / 'backend/alembic.ini'))
        with engine.connect() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.commit()
    else:
        engine = create_engine(f"sqlite:///{tmp_path / 'outbox.db'}",
                               connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def enable_fks(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

        Base.metadata.create_all(engine)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data",
                        outbox_max_retries=2, task_max_retries=3,
                        outbox_poll_seconds=1, task_retry_base_seconds=5)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield engine, factory, settings
    engine.dispose()
    if root_engine is not None:
        with root_engine.begin() as connection:
            connection.execute(text(f'DROP DATABASE `{database}`'))


def seed(factory, settings, *, status="pending"):
    document_id, task_id = new_id(), new_id()
    with factory() as session, session.begin():
        session.add(Document(id=document_id, original_filename="a.pdf",
                             storage_key=f"uploads/{document_id}.pdf", sha256="a" * 64,
                             size_bytes=10))
        session.flush()
        session.add(ParseTask(id=task_id, document_id=document_id, version=1,
                              status=status, stage="queued" if status == "pending" else "parsing",
                              attempts=0, max_retries=settings.task_max_retries))
        session.flush()
        add_parse_requested(session, settings, ParseEvent(
            event_id=new_id(), document_id=document_id, task_id=task_id,
            version=1, storage_key=f"uploads/{document_id}.pdf"))
    return document_id, task_id


def read_event(factory):
    with Session(factory.kw["bind"]) as session:
        return session.scalar(select(OutboxEvent).where(OutboxEvent.event_type == "pdf.parse.requested"))


def test_kafka_unavailable_keeps_event_pending_and_retries_later(outbox_env):
    engine, factory, settings = outbox_env
    document_id, task_id = seed(factory, settings)
    failing = FakePublisher(PublishError("Kafka down"))
    dispatcher = OutboxDispatcher(settings, factory, failing, owner="dispatcher-1")
    now = utcnow()

    stats = dispatcher.run_once(now=now)
    assert stats["claimed"] == 1 and stats["requeued"] == 1
    stored = read_event(factory)
    assert stored.status == "pending" and stored.attempts == 1
    assert stored.available_at > now
    with factory() as session:
        task = session.get(ParseTask, task_id)
        assert task.status == "pending" and task.error_code is None

    # Not due yet: no early redelivery.
    assert dispatcher.run_once(now=now + timedelta(seconds=1))["claimed"] == 0
    # Kafka recovers; the same durable event is published and acknowledged.
    failing.error = None
    stats = dispatcher.run_once(now=now + timedelta(seconds=10))
    assert stats["published"] == 1
    stored = read_event(factory)
    assert stored.status == "published" and stored.published_at is not None
    assert failing.calls[0][0] == settings.kafka_pdf_topic
    assert failing.calls[0][1] == task_id
    # Already published events are never duplicated by the dispatcher.
    dispatcher.run_once(now=now + timedelta(seconds=20))
    assert len(failing.calls) == 1


def test_outbox_exhaustion_fails_task_and_creates_failure_event(outbox_env):
    engine, factory, settings = outbox_env
    document_id, task_id = seed(factory, settings)
    failing = FakePublisher(PublishError("Kafka down"))
    dispatcher = OutboxDispatcher(settings, factory, failing, owner="dispatcher-1")
    now = utcnow()

    dispatcher.run_once(now=now)
    stats = dispatcher.run_once(now=now + timedelta(seconds=10))
    assert stats["failed"] >= 1
    with factory() as session:
        request_event = session.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "pdf.parse.requested"))
        failed_event = session.scalar(select(OutboxEvent).where(
            OutboxEvent.event_type == "pdf.parse.failed"))
        task = session.get(ParseTask, task_id)
        assert request_event.status == "failed"
        assert task.status == "failed" and task.stage == "failed"
        assert task.error_code == "OUTBOX_EXHAUSTED"
        assert failed_event.status == "pending"
        assert failed_event.topic == settings.kafka_failed_topic
    # The failure event is still recoverable and uses the same durable outbox.
    failing.error = None
    assert dispatcher.run_once(now=now + timedelta(seconds=30))["published"] == 1


def test_expired_publishing_lease_is_reclaimed_after_dispatcher_crash(outbox_env):
    engine, factory, settings = outbox_env
    document_id, task_id = seed(factory, settings)
    now = utcnow()
    with factory() as session, session.begin():
        stored = session.scalar(select(OutboxEvent))
        stored.status = "publishing"
        stored.attempts = 1
        stored.lease_owner = "dead-dispatcher"
        stored.lease_token = new_id()
        stored.lease_expires_at = now - timedelta(seconds=1)
    publisher = FakePublisher()
    stats = OutboxDispatcher(settings, factory, publisher, owner="dispatcher-2").run_once(now=now)
    assert stats["published"] == 1
    assert len(publisher.calls) == 1
    assert read_event(factory).status == "published"


def test_publish_acknowledged_but_mark_lost_is_redelivered(outbox_env, monkeypatch):
    from app.workers import outbox_dispatcher
    _, factory, settings = outbox_env
    seed(factory, settings)
    publisher = FakePublisher()
    dispatcher = OutboxDispatcher(settings, factory, publisher, owner='before-crash')
    original = outbox_dispatcher.mark_published
    def lost_ack(*args, **kwargs):
        raise RuntimeError('Lost DB acknowledgement after broker confirmation')
    monkeypatch.setattr(outbox_dispatcher, 'mark_published', lost_ack)
    now = utcnow()
    with pytest.raises(RuntimeError, match='Lost DB'):
        dispatcher.run_once(now=now)
    assert read_event(factory).status == 'publishing'
    monkeypatch.setattr(outbox_dispatcher, 'mark_published', original)
    assert dispatcher.run_once(now=now + timedelta(seconds=settings.outbox_lease_seconds + 1))['published'] == 1
    assert len(publisher.calls) == 2 and publisher.calls[0] == publisher.calls[1]
