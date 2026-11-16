import hashlib
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.base import Base
from app.main import create_app
from app.models import Document, OutboxEvent, ParseTask
from app.services.storage import FileStorage
from app.services.upload_recovery import reconcile_uploads
from app.services import uploads


def pdf_bytes():
    # A real, tiny PDF container, never sent to a PDF parser during upload.
    body = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, value in enumerate((
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >>',
    ), 1):
        offsets.append(len(body))
        body.extend(f'{index} 0 obj\n'.encode() + value + b'\nendobj\n')
    xref = len(body)
    body.extend(b'xref\n0 4\n0000000000 65535 f \n')
    for offset in offsets[1:]:
        body.extend(f'{offset:010d} 00000 n \n'.encode())
    body.extend(f'trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode())
    return bytes(body)


PDF = pdf_bytes()


@pytest.fixture
def upload_app(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}', connect_args={'check_same_thread': False})

    @event.listens_for(engine, 'connect')
    def enable_fks(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')

    Base.metadata.create_all(engine)
    monkeypatch.setattr('app.main.create_db_engine', lambda _: engine)
    settings = Settings(_env_file=None, data_dir=tmp_path / 'data')
    app = create_app(settings)
    with TestClient(app) as client:
        yield client, app, engine, settings


def counts(engine):
    with Session(engine) as session:
        return tuple(session.scalar(select(func.count()).select_from(model))
                     for model in (Document, ParseTask, OutboxEvent))


def assert_upload_traceable(client, engine, settings):
    result = client.post('/api/documents/batch', files=[
        ('files', ('相同名.pdf', PDF, 'application/pdf')),
        ('files', ('相同名.pdf', PDF, 'application/pdf')),
    ])
    assert result.status_code == 200, result.text
    body = result.json()
    assert body['accepted'] == 2 and body['rejected'] == 0
    assert body['request_id'] == result.headers['X-Request-ID']
    documents = [item['document'] for item in body['results']]
    assert documents[0]['id'] != documents[1]['id']
    for document in documents:
        task = document['latest_task']
        assert task['status'] == 'pending' and task['stage'] == 'queued'
        assert task['version'] == 1 and task['attempts'] == 0
        assert document['current_searchable_version'] is None
        assert document['created_at'].endswith('Z')
        assert client.get(f'/api/documents/{document["id"]}').json() == document
        assert client.get(f'/api/tasks/{task["id"]}').json() == task
        assert 'storage_key' not in document and 'sha256' not in document
        with Session(engine) as session:
            stored = session.get(Document, document['id'])
            assert stored.sha256 == hashlib.sha256(PDF).hexdigest()
            assert (settings.data_dir / stored.storage_key).read_bytes() == PDF
            outbox = session.scalar(select(OutboxEvent).where(OutboxEvent.task_id == task['id']))
            assert outbox.status == 'pending' and outbox.topic == settings.kafka_pdf_topic
            assert outbox.payload == dict(schema_version=1, event_id=outbox.id,
                task_id=task['id'], document_id=document['id'], version=1, storage_key=stored.storage_key)
    page = client.get('/api/documents?page_size=1&status=pending').json()
    assert page['total'] >= 2 and len(page['items']) == 1
    assert client.get('/api/documents?status=completed').json()['items'] == []
    assert list((settings.data_dir / 'tmp').rglob('*')) == [settings.data_dir / 'tmp' / 'upload-recovery']
    return documents


def test_batch_upload_traceability_and_queries(upload_app, monkeypatch):
    client, app, engine, settings = upload_app

    def no_process(*args, **kwargs):
        pytest.fail('Uploading must never start MinerU or another subprocess')

    monkeypatch.setattr('subprocess.Popen', no_process)
    documents = assert_upload_traceable(client, engine, settings)
    assert counts(engine) == (2, 2, 2)
    assert client.get('/api/documents?page=99').json()['items'] == []
    assert client.get('/api/documents?page=0').status_code == 422
    assert client.get('/api/documents?page_size=101').status_code == 422
    assert client.get('/api/documents?status=unknown').status_code == 422
    for endpoint, code in (('documents', 'DOCUMENT_NOT_FOUND'), ('tasks', 'TASK_NOT_FOUND')):
        response = client.get(f'/api/{endpoint}/{uuid4()}')
        assert response.status_code == 404 and response.json()['error']['code'] == code
        assert client.get(f'/api/{endpoint}/bad-id').status_code == 422
    # Query the latest version but retain access to historical tasks, from MySQL/DB rather than Redis.
    with Session(engine) as session, session.begin():
        doc = session.get(Document, documents[0]['id'])
        task_id = str(uuid4())
        session.add(ParseTask(id=task_id, document_id=doc.id, version=2, status='failed',
                             stage='failed', max_retries=3, error_code='PARSE_FAILED', error_message='解析失败'))
        session.flush()
        doc.latest_version = 2
    assert client.get(f'/api/documents/{documents[0]["id"]}').json()['latest_task']['id'] == task_id
    assert client.get('/api/documents?status=failed').json()['total'] == 1
    assert client.get(f'/api/tasks/{documents[0]["latest_task"]["id"]}').json()['version'] == 1


def test_invalid_files_are_individually_rejected_and_paths_are_internal(upload_app):
    client, app, engine, settings = upload_app
    settings.upload_max_file_bytes = len(PDF)
    response = client.post('/api/documents/batch', files=[
        ('files', ('bad.txt', PDF, 'application/pdf')),
        ('files', ('fake.pdf', b'not a PDF', 'application/pdf')),
        ('files', ('empty.pdf', b'', 'application/pdf')),
        ('files', ('large.pdf', PDF + b' ', 'application/pdf')),
        ('files', ('wrong.pdf', PDF, 'image/png')),
        ('files', ('truncated.pdf', PDF[:-12], 'application/pdf')),
        ('files', ('../escape.PDF', PDF, 'application/octet-stream')),
    ])
    assert response.status_code == 200
    results = response.json()['results']
    assert [r['index'] for r in results] == list(range(7))
    assert [r['status_code'] for r in results] == [415, 415, 422, 413, 415, 415, 202]
    assert counts(engine) == (1, 1, 1)
    document = results[-1]['document']
    assert document['original_filename'] == 'escape.PDF'
    assert not (settings.data_dir / 'escape.PDF').exists()
    assert (settings.data_dir / 'uploads' / f'{document["id"]}.pdf').read_bytes() == PDF
    all_bad = client.post('/api/documents/batch', files=[('files', ('x.pdf', b'fake', 'application/pdf'))])
    assert all_bad.status_code == 200 and all_bad.json()['accepted'] == 0


@pytest.mark.parametrize('content_length', [None, '1'])
def test_request_limit_streamed_without_trusting_length(upload_app, content_length):
    client, app, engine, settings = upload_app
    settings.upload_max_request_bytes = len(PDF) + 80
    settings.upload_max_file_bytes = len(PDF)
    req = httpx.Request('POST', '/api/documents/batch', files=[('files', ('valid.pdf', PDF, 'application/pdf'))])
    body = req.read()
    assert len(body) > settings.upload_max_request_bytes  # Multipart overhead counts too.
    headers = {'Content-Type': req.headers['Content-Type']}
    if content_length is not None:
        headers['Content-Length'] = content_length
    response = client.post('/api/documents/batch', content=iter([body[:50], body[50:]]), headers=headers)
    assert response.status_code == 413 and response.json()['error']['code'] == 'REQUEST_TOO_LARGE'
    assert counts(engine) == (0, 0, 0)
    assert not list(settings.data_dir.rglob('*.pdf'))


def test_count_and_malformed_multipart_reject_whole_request(upload_app):
    client, app, engine, settings = upload_app
    settings.upload_max_files = 1
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))] * 2)
    assert response.status_code == 422 and response.json()['error']['code'] == 'FILE_COUNT_EXCEEDED'
    req = httpx.Request('POST', '/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    body = req.read()
    response = client.post('/api/documents/batch', content=body[:-12], headers={'Content-Type': req.headers['Content-Type']})
    assert response.status_code == 400
    assert client.post('/api/documents/batch', content=b'x', headers={'Content-Type': 'multipart/form-data'}).status_code == 400
    assert client.post('/api/documents/batch', json={}).status_code == 415
    assert counts(engine) == (0, 0, 0)
    assert not list(settings.data_dir.rglob('*.pdf'))


def test_database_unavailable_before_processing_returns_request_error(upload_app, monkeypatch):
    client, app, engine, settings = upload_app

    def unavailable(*args, **kwargs):
        raise OperationalError('private SQL', {}, Exception('private password'))

    monkeypatch.setattr(engine, 'connect', unavailable)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    assert response.status_code == 503 and response.json()['error']['code'] == 'DATABASE_NOT_READY'
    assert 'private' not in response.text
    assert not list(settings.data_dir.rglob('*.pdf'))
    assert client.get('/api/documents').status_code == 503


def test_outbox_failure_rolls_back_document_task_and_cleans_only_failed_file(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    original = uploads.add_parse_requested
    calls = 0

    def fail_second(*args):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OperationalError('private SQL', {}, Exception('private password'))
        return original(*args)

    monkeypatch.setattr(uploads, 'add_parse_requested', fail_second)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))] * 3)
    assert [r['status_code'] for r in response.json()['results']] == [202, 503, 202]
    assert counts(engine) == (2, 2, 2)
    assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 2
    assert not list(FileStorage(settings).recovery_dir.glob('*.json'))
    assert 'private' not in response.text


def test_storage_write_failure_cleans_partial_file(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    original = FileStorage.save_pdf

    def fail_after_save(self, *args):
        original(self, *args)
        raise OSError('private path')

    monkeypatch.setattr(FileStorage, 'save_pdf', fail_after_save)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    assert response.json()['results'][0]['error']['code'] == 'STORAGE_UNAVAILABLE'
    assert counts(engine) == (0, 0, 0)
    assert not list(settings.data_dir.rglob('*.pdf'))
    assert not list(settings.data_dir.rglob('*.json'))


def test_commit_failure_preserves_receipt_and_offline_recovery_cleans_orphan(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    factory = app.state.session_factory
    original_commit = engine.dialect.do_commit

    def fail_commit(connection):
        # Preflight is read-only, so fail only if the connection has pending writes.
        if connection.in_transaction:
            raise OperationalError('COMMIT', {}, Exception('connection lost'))
        return original_commit(connection)

    monkeypatch.setattr(engine.dialect, 'do_commit', fail_commit)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    error = response.json()['results'][0]['error']
    assert error['code'] == 'UPLOAD_COMMIT_UNCERTAIN' and not error['retryable']
    monkeypatch.setattr(engine.dialect, 'do_commit', original_commit)
    assert counts(engine) == (0, 0, 0)
    storage = FileStorage(settings)
    assert len(list(storage.recovery_dir.glob('*.json'))) == 1
    assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 1
    assert reconcile_uploads(storage, factory) == {'referenced': 0, 'orphaned': 1, 'errors': 0}
    assert len(list(storage.recovery_dir.glob('*.json'))) == 1  # Dry-run preserves files.
    assert reconcile_uploads(storage, factory, apply=True)['orphaned'] == 1
    assert not list(settings.data_dir.rglob('*.pdf'))
    assert not list(storage.recovery_dir.glob('*.json'))


def test_lost_commit_acknowledgement_confirms_success_without_deleting_pdf(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    original_commit = engine.dialect.do_commit

    def commit_then_disconnect(connection):
        wrote = connection.in_transaction
        original_commit(connection)
        if wrote:
            raise OperationalError('COMMIT', {}, Exception('acknowledgement lost'))

    monkeypatch.setattr(engine.dialect, 'do_commit', commit_then_disconnect)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    assert response.json()['accepted'] == 1
    assert counts(engine) == (1, 1, 1)
    assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 1
    assert not list(FileStorage(settings).recovery_dir.glob('*.json'))


def test_cleanup_failure_receipt_recovers_and_preserves_committed_file(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    original_unlink = Path.unlink

    def cannot_unlink(path, *args, **kwargs):
        if path.suffix == '.json':
            raise PermissionError('test locked receipt')
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'unlink', cannot_unlink)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    assert response.json()['accepted'] == 1
    storage = FileStorage(settings)
    assert len(list(storage.recovery_dir.glob('*.json'))) == 1
    monkeypatch.setattr(Path, 'unlink', original_unlink)
    assert reconcile_uploads(storage, app.state.session_factory, apply=True)['referenced'] == 1
    assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 1
    assert not list(storage.recovery_dir.glob('*.json'))


def test_failed_file_unlink_is_recovered_after_database_rollback(upload_app, monkeypatch):
    client, app, engine, settings = upload_app
    original_unlink = Path.unlink

    def cannot_unlink_pdf(path, *args, **kwargs):
        if path.suffix == '.pdf':
            raise PermissionError('locked file')
        return original_unlink(path, *args, **kwargs)

    def outbox_failed(*args):
        raise OperationalError('INSERT', {}, Exception('outbox failure'))

    monkeypatch.setattr(Path, 'unlink', cannot_unlink_pdf)
    monkeypatch.setattr(uploads, 'add_parse_requested', outbox_failed)
    response = client.post('/api/documents/batch', files=[('files', ('x.pdf', PDF, 'application/pdf'))])
    assert response.json()['rejected'] == 1
    assert counts(engine) == (0, 0, 0)
    storage = FileStorage(settings)
    assert len(list(storage.recovery_dir.glob('*.json'))) == 1
    assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 1
    monkeypatch.setattr(Path, 'unlink', original_unlink)
    assert reconcile_uploads(storage, app.state.session_factory, apply=True) == {
        'referenced': 0, 'orphaned': 1, 'errors': 0}
    assert not list(settings.data_dir.rglob('*.pdf'))
    assert not list(storage.recovery_dir.glob('*.json'))


def test_real_mysql_upload_traceability(isolated_mysql, tmp_path, monkeypatch):
    engine, base_settings = isolated_mysql
    config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
    with engine.connect() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head')
        connection.commit()
    settings = base_settings.model_copy(update={'data_dir': tmp_path / 'data'})
    with TestClient(create_app(settings)) as client:
        assert_upload_traceable(client, engine, settings)
        before = counts(engine)

        def outbox_failed(*args):
            raise OperationalError('INSERT', {}, Exception('simulated outbox failure'))

        monkeypatch.setattr(uploads, 'add_parse_requested', outbox_failed)
        response = client.post('/api/documents/batch', files=[('files', ('rollback.pdf', PDF, 'application/pdf'))])
        assert response.json()['results'][0]['status_code'] == 503
        assert counts(engine) == before
        assert len(list((settings.data_dir / 'uploads').glob('*.pdf'))) == 2
        assert not list(FileStorage(settings).recovery_dir.glob('*.json'))
