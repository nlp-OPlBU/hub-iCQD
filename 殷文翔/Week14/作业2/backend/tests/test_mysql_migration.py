"""Opt-in real MySQL tests: a temporary private instance; never the .env database.

Set RAG_TEST_MYSQL_BINARY to a local mysqld executable to enable.
"""
from pathlib import Path
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.db.base import new_id, utcnow
from app.main import create_app
from app.models import Conversation, Document, ImageResource, Message, OutboxEvent, ParseTask



def test_real_mysql_upgrade_constraints_readiness_and_downgrade(isolated_mysql):
    engine, settings = isolated_mysql
    config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
    with engine.connect() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        connection.commit()
        command.upgrade(config, 'head')
        connection.commit()
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == '0001_initial'
        connection.commit()
        command.check(config)
        connection.commit()
    assert set(inspect(engine).get_table_names()) == {
        'alembic_version', 'documents', 'parse_tasks', 'outbox_events', 'conversations', 'messages', 'image_resources'}
    doc_id, task_id, conversation_id = new_id(), new_id(), new_id()
    with Session(engine) as session, session.begin():
        session.add(Document(id=doc_id, original_filename='样例.pdf', storage_key='uploads/test.pdf',
                             sha256='a' * 64, size_bytes=100))
        session.flush()
        session.add(ParseTask(id=task_id, document_id=doc_id, version=1, max_retries=3))
        session.flush()
        session.add(OutboxEvent(task_id=task_id, document_id=doc_id, version=1,
                               event_type='pdf.parse.requested', topic='test-only', payload={}, max_retries=10))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(ParseTask(document_id=doc_id, version=1, max_retries=3))
    with pytest.raises(OperationalError, match='3819'), Session(engine) as session, session.begin():
        session.add(ParseTask(document_id=doc_id, version=2, status='invalid', max_retries=3))
    with pytest.raises(IntegrityError), Session(engine) as session, session.begin():
        session.add(OutboxEvent(task_id=task_id, document_id=doc_id, version=2,
                               event_type='pdf.parse.failed', topic='test-only', payload={}, max_retries=10))
    with Session(engine) as session, session.begin():
        task = session.get(ParseTask, task_id)
        task.status, task.stage = 'completed', 'done'
        task.artifact_key, task.index_name, task.indexed_at = 'parsed/test.zip', 'test-index', utcnow()
        session.get(Document, doc_id).current_searchable_version = 1
        session.add(Conversation(id=conversation_id, title='中文会话'))
        session.flush()
        session.add(Message(conversation_id=conversation_id, sequence=1, request_key='test',
                            request_hash='b' * 64, role='user', status='completed', content='😀' * 20000))
        session.add(ImageResource(kind='user_upload', storage_key='images/user.png', media_type='image/png',
                                 size_bytes=5, width=1, height=1, sha256='c' * 64, conversation_id=conversation_id))
        session.add(ParseTask(document_id=doc_id, version=2, status='failed', max_retries=3))
        session.get(Document, doc_id).latest_version = 2
    with Session(engine) as session:
        assert session.get(Document, doc_id).current_searchable_version == 1
        assert session.scalar(select(Message.content)) == '😀' * 20000
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/health/ready').status_code == 200
    with engine.connect() as connection:
        config.attributes['connection'] = connection
        command.downgrade(config, 'base')
        connection.commit()
        assert inspect(connection).get_table_names() == ['alembic_version']
        command.upgrade(config, 'head')
        connection.commit()
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/health/ready').status_code == 200
