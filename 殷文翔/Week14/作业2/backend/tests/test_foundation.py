import io
import json
import logging
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.core.config import PROJECT_ROOT, Settings
from app.core.logging import JsonFormatter
from app.main import create_app
from app.schemas.chat import QuestionRequest
from app.schemas.document import BatchUploadResponse


def test_config_example_and_root_relative_paths(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    settings = Settings(_env_file=PROJECT_ROOT / '.env.example')
    assert settings.data_dir == PROJECT_ROOT / 'data'
    assert settings.log_dir == PROJECT_ROOT / 'logs'
    assert settings.app_port == 8001
    assert settings.retrieval_mode == 'bm25'
    assert not settings.rerank_enabled and not settings.llm_vision_enabled
    assert settings.es_ca_certs is None


def test_env_overrides_dotenv_and_secret_url(monkeypatch, tmp_path):
    env = tmp_path / '.env'
    env.write_text('APP_PORT=9000\nMYSQL_PASSWORD=p@ss%/word\n', encoding='utf-8')
    monkeypatch.setenv('APP_PORT', '9001')
    settings = Settings(_env_file=env)
    assert settings.app_port == 9001
    assert settings.mysql_url.password == 'p@ss%/word'
    assert 'p@ss%/word' not in repr(settings)


@pytest.mark.parametrize('options', [
    {'task_lease_seconds': 60, 'task_heartbeat_seconds': 30},
    {'upload_max_files': 0}, {'zip_max_entry_bytes': 200, 'zip_max_uncompressed_bytes': 100},
    {'task_max_retries': -1}, {'image_token_budget': 12000},
    {'retrieval_mode': 'hybrid', 'embedding_model': ''},
])
def test_invalid_config_fails_fast(options):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **options)


def test_health_errors_and_no_automatic_database_connection(monkeypatch):
    app = create_app(Settings(_env_file=None))
    with TestClient(app, raise_server_exceptions=False) as client:
        def unavailable():
            raise OperationalError('secret SQL', {}, Exception('secret password'))
        monkeypatch.setattr(app.state.engine, 'connect', unavailable)
        response = client.get('/api/health')
        assert response.status_code == 200
        assert response.json()['scope'] == 'process'
        assert response.headers['X-Request-ID']
        ready = client.get('/api/health/ready')
        assert ready.status_code == 503
        assert ready.json()['error']['code'] == 'DATABASE_NOT_READY'
        assert 'secret' not in ready.text
        missing = client.get('/api/not-an-endpoint')
        assert missing.status_code == 404
        assert missing.json()['request_id'] == missing.headers['X-Request-ID']
        assert '/api/health' in client.get('/api/openapi.json').json()['paths']


@pytest.mark.parametrize('vision', [False, True])
def test_public_capabilities_follow_config_without_connecting_to_services(monkeypatch, vision):
    app = create_app(Settings(_env_file=None, llm_vision_enabled=vision,
                              upload_max_files=3, image_max_upload_files=2,
                              mysql_password='private-test-password'))
    def unavailable():
        raise AssertionError('Capabilities must not connect to the database')
    with TestClient(app) as client:
        monkeypatch.setattr(app.state.engine, 'connect', unavailable)
        response = client.get('/api/capabilities')
        assert response.status_code == 200
        assert response.json() == {
            'vision_enabled': vision, 'upload_max_files': 3,
            'upload_max_file_bytes': 52428800, 'upload_max_request_bytes': 209715200,
            'image_max_upload_files': 2, 'image_max_file_bytes': 10485760,
        }
        assert 'private-test-password' not in response.text


def test_validation_and_unexpected_error_do_not_expose_inputs():
    app = create_app(Settings(_env_file=None))

    @app.get('/test/validation')
    def validation(number: int):
        return number

    @app.get('/test/failure')
    def failure():
        raise RuntimeError('private-password')

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get('/test/validation?number=private-password')
        assert response.status_code == 422
        assert 'private-password' not in response.text
        response = client.get('/test/failure')
        assert response.status_code == 500
        assert response.json()['error']['code'] == 'INTERNAL_ERROR'
        assert response.headers['X-Request-ID'] == response.json()['request_id']
        assert 'private-password' not in response.text


def test_json_log_redaction():
    record = logging.LogRecord('test', logging.ERROR, '', 0,
        'mysql://user:abc123@host db-secret token=other-secret Authorization: Bearer hidden', (), None)
    output = JsonFormatter(['db-secret']).format(record)
    assert json.loads(output)['level'] == 'ERROR'
    for secret in ('abc123', 'db-secret', 'other-secret', 'hidden'):
        assert secret not in output


def test_contracts_reject_ambiguous_questions_and_batch_positions():
    with pytest.raises(ValidationError):
        QuestionRequest(text='  ')
    with pytest.raises(ValidationError):
        QuestionRequest(text='question', document_ids=[])
    assert QuestionRequest(image_ids=[uuid4()]).text == ''
    result = {'index': 0, 'filename': 'bad.txt', 'outcome': 'rejected', 'status_code': 415,
              'error': {'code': 'INVALID_FILE_TYPE', 'message': '仅允许 PDF'}}
    batch = BatchUploadResponse(request_id='test', accepted=0, rejected=1, results=[result])
    assert batch.results[0].outcome == 'rejected'
    with pytest.raises(ValidationError):
        BatchUploadResponse(request_id='test', accepted=1, rejected=0, results=[result])


def test_mysql_offline_migration_has_all_tables_and_deferred_fk():
    output = io.StringIO()
    config = Config(str(Path(__file__).parents[1] / 'alembic.ini'), output_buffer=output)
    command.upgrade(config, 'head', sql=True)
    sql = output.getvalue()
    for name in ('documents', 'parse_tasks', 'outbox_events', 'conversations', 'messages', 'image_resources'):
        assert f'CREATE TABLE {name}' in sql
    assert 'utf8mb4' in sql and 'DATETIME(6)' in sql
    assert sql.index('CREATE TABLE parse_tasks') < sql.index('ALTER TABLE documents ADD CONSTRAINT fk_document_searchable_task')
