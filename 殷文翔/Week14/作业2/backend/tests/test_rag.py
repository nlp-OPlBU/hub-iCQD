"""API and actual local HTTP payload tests; optionally repeat with isolated MySQL."""
import base64
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from PIL import Image
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.errors import ApiError
from app.core.config import Settings
from app.db.base import Base, new_id, utcnow
from app.db.session import create_db_engine
from app.main import create_app
from app.models import Document, ImageResource, Message, ParseTask
from app.services.elasticsearch_store import ElasticsearchStore
from app.services.images import decode_image, resource_path
from app.services.llm_client import LLMClient


def png(size=(200, 100)):
    stream = io.BytesIO()
    Image.new('RGB', size, 'blue').save(stream, format='PNG')
    return stream.getvalue()


@pytest.mark.parametrize('reference,expected', [('S1', 'completed'), ('S9', 'failed'),
                                             ('S1 和 S9', 'failed')])
def test_explicit_unbracketed_model_sources_are_validated(chat_api, monkeypatch, reference, expected):
    monkeypatch.setattr(LLMClient, 'complete', lambda *args, **kwargs:
                        f'依据检索证据 {reference}，购物车最多添加100件商品。')
    response = chat_api.ask({'text': '购物车最多多少件？', 'document_ids': [chat_api.doc1]})
    assert response.status_code == 200
    answer = response.json()['assistant_message']
    assert answer['status'] == expected
    if expected == 'completed':
        assert '[S1]' in answer['content'] and answer['sources'][0]['source_id'] == 'S1'
    else:
        assert answer['error']['code'] == 'INVALID_MODEL_CITATION' and not answer['sources']


def test_code_literals_and_unattributed_source_tokens_do_not_create_citations(chat_api, monkeypatch):
    monkeypatch.setattr(LLMClient, 'complete', lambda *args, **kwargs:
                        'S1 是变量名。`来源 S1`\n```text\n证据 S1\n```')
    answer = chat_api.ask({'text': '代码含义？', 'document_ids': [chat_api.doc1]}).json()['assistant_message']
    assert answer['status'] == 'completed' and not answer['sources']
    assert '依据不足' in answer['content']


@pytest.fixture
def model_server():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(body)
            is_description = '检索关键词' in body['messages'][0]['content']
            result = {'choices': [{'message': {'content': '蓝色 图表 购物车' if is_description else
                       '购物车最多容纳 100 件商品。[S1]\n<script>alert(1)</script>'}, 'finish_reason': 'stop'}]}
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(json.dumps(result).encode())
        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/v1', requests
    server.shutdown(); server.server_close(); thread.join()


@pytest.fixture(params=['sqlite', 'mysql'])
def chat_api(request, tmp_path, monkeypatch, model_server):
    settings = Settings(_env_file=None, data_dir=tmp_path, llm_base_url=model_server[0],
                        llm_vision_enabled=True, retrieval_top_k=2)
    root_engine = None
    if request.param == 'mysql':
        root_engine, mysql_settings = request.getfixturevalue('isolated_mysql')
        database = 'rag_chat_' + uuid4().hex
        with root_engine.begin() as connection:
            connection.execute(text(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4'))
        settings = settings.model_copy(update={field: getattr(mysql_settings, field) for field in (
            'mysql_host', 'mysql_port', 'mysql_user', 'mysql_password')})
        settings.mysql_database = database
        engine = create_db_engine(settings)
        config = Config(str(Path(__file__).parents[1] / 'alembic.ini'))
        with engine.connect() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head'); connection.commit()
    else:
        engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    doc1, doc2 = new_id(), new_id()
    key = f'parsed/{doc1}/v1/attempt'
    directory = tmp_path / key
    directory.mkdir(parents=True)
    (directory / 'image.png').write_bytes(png())
    (directory / 'normalized.json').write_text(json.dumps({'pages': [
        {'page': 2, 'blocks': [{'image_keys': [key + '/image.png']}]}]}))
    with factory() as session, session.begin():
        for doc in (doc1, doc2):
            session.add(Document(id=doc, original_filename=doc + '.pdf', storage_key=doc,
                                 sha256='a' * 64, size_bytes=10))
        session.flush()
        for doc in (doc1, doc2):
            session.add(ParseTask(id=new_id(), document_id=doc, version=1, status='completed',
                max_retries=0, artifact_key=key if doc == doc1 else None, retrieval_mode='bm25',
                index_name=settings.es_index))
        session.flush()
        for doc in (doc1, doc2):
            session.get(Document, doc).current_searchable_version = 1
    hit = {'document_id': doc1, 'version': 1, 'filename': 'UNTRUSTED.pdf', 'page': 2,
           'chunk_id': doc1 + ':1:2:0:0', 'text': '购物车可以添加100件商品。', 'score': 9.5,
           'image_keys': [], 'page_image_keys': [key + '/image.png']}
    calls = []
    def search(_self, query, *, versions, limit):
        calls.append((query, dict(versions)))
        return [hit] if versions.get(doc1) == 1 else []
    monkeypatch.setattr(ElasticsearchStore, 'search', search)
    app = create_app(settings)
    try:
        with TestClient(app) as client:
            app.state.session_factory = factory
            conversation = client.post('/api/conversations', json={'title': '测试会话'}).json()['id']
            yield SimpleNamespace(client=client, factory=factory, settings=settings, hit=hit,
                calls=calls, model_calls=model_server[1], conversation=conversation, doc1=doc1, doc2=doc2,
                ask=lambda body, key=None: client.post(f'/api/conversations/{conversation}/messages',
                    json=body, headers={'Idempotency-Key': key or new_id()}))
    finally:
        engine.dispose()
        if root_engine is not None:
            with root_engine.begin() as connection:
                connection.execute(text(f'DROP DATABASE `{database}`'))


def test_scoped_text_answer_and_durable_sources(chat_api):
    api = chat_api
    api.settings.llm_vision_enabled = False
    response = api.ask({'text': '购物车上限', 'document_ids': [api.doc1]})
    assert response.status_code == 200, response.text
    answer = response.json()['assistant_message']
    assert answer['status'] == 'completed', answer
    source = answer['sources'][0]
    assert source['source_id'] == 'S1' and source['page'] == 2 and source['document_id'] == api.doc1
    assert source['filename'] == api.doc1 + '.pdf'
    assert api.calls[0][1] == {api.doc1: 1}
    assert len(api.model_calls) == 1
    assert all(part['type'] == 'text' for part in api.model_calls[0]['messages'][1]['content'])
    image = source['images'][0]
    content = api.client.get(image['url'])
    assert content.content == png() and content.headers['x-content-type-options'] == 'nosniff'
    assert content.headers['cache-control'].startswith('private')
    stored = api.client.get(f'/api/conversations/{api.conversation}/messages').json()['items']
    assert [item['sequence'] for item in stored] == [1, 2] and stored[1]['sources'] == answer['sources']
    with api.factory() as session:
        row = session.get(Message, answer['id'])
        assert row.document_scope == [{'document_id': api.doc1, 'version': 1}]


def test_explicit_scope_no_evidence_does_not_call_model(chat_api):
    api = chat_api
    answer = api.ask({'text': '购物车', 'document_ids': [api.doc2]}).json()['assistant_message']
    assert '依据不足' in answer['content'] and answer['sources'] == [] and api.model_calls == []
    assert api.calls[0][1] == {api.doc2: 1}
    assert api.ask({'text': '问题', 'document_ids': []}).status_code == 422
    assert api.ask({'text': '问题', 'document_ids': [new_id()]}).json()['error']['code'] == 'INVALID_DOCUMENT_SCOPE'


def test_real_http_image_only_description_and_final_user_plus_document_images(chat_api):
    api = chat_api
    upload = api.client.post(f'/api/conversations/{api.conversation}/images',
                             files={'files': ('not-trusted-name.txt', png(), 'text/plain')})
    assert upload.status_code == 201, upload.text
    image = upload.json()['items'][0]
    response = api.ask({'image_ids': [image['id']], 'document_ids': [api.doc1]})
    answer = response.json()['assistant_message']
    assert answer['status'] == 'completed', answer
    assert '蓝色 图表' in api.calls[0][0]  # image-derived query works without question text
    assert len(api.model_calls) == 2
    for index, expected in enumerate((1, 2)):
        parts = api.model_calls[index]['messages'][1]['content']
        images = [part for part in parts if part['type'] == 'image_url']
        assert len(images) == expected
        for part in images:
            data = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
            with Image.open(io.BytesIO(data)) as decoded:
                assert decoded.size == (200, 100)
                assert decoded.getpixel((10, 10))[2] > 200  # actual blue pixels, not a path
    assert image['url'] in api.client.get(f'/api/conversations/{api.conversation}/messages').text
    assert answer['sources'][0]['images'][0]['url'] in answer['content']


def test_idempotency_conflicts_and_failures_are_durable(chat_api, monkeypatch):
    api = chat_api
    key, body = new_id(), {'text': '购物车', 'document_ids': [api.doc1]}
    first = api.ask(body, key).json()
    count = len(api.model_calls)
    assert api.ask(body, key).json() == first and len(api.model_calls) == count
    assert api.ask({'text': '另一个问题'}, key).status_code == 409
    def unavailable(*_args, **_kwargs):
        raise ApiError(504, 'MODEL_TIMEOUT', '模型响应超时')
    monkeypatch.setattr(LLMClient, 'complete', unavailable)
    failed_key = new_id()
    failed = api.ask(body, failed_key).json()
    assert failed['assistant_message']['error']['code'] == 'MODEL_TIMEOUT'
    assert api.ask(body, failed_key).json() == failed


def test_cross_conversation_invalid_images_and_atomic_batch(chat_api):
    api = chat_api
    path = f'/api/conversations/{api.conversation}/images'
    upload = api.client.post(path, files={'files': ('a.png', png(), 'image/png')}).json()['items'][0]
    other = api.client.post('/api/conversations', json={}).json()['id']
    response = api.client.post(f'/api/conversations/{other}/messages', json={'image_ids': [upload['id']]},
                               headers={'Idempotency-Key': new_id()})
    assert response.status_code == 422 and response.json()['error']['code'] == 'INVALID_IMAGE_SCOPE'
    failed = api.client.post(path, files=[('files', ('ok.png', png(), 'image/png')),
                                         ('files', ('bad.svg', b'<svg/>', 'image/svg+xml'))])
    assert failed.status_code == 415
    with api.factory() as session:
        assert len(session.scalars(select(ImageResource).where(ImageResource.kind == 'user_upload')).all()) == 1
    assert api.client.get('/api/images/' + new_id() + '/content').status_code == 404


def test_vision_switch_and_budget_enforced(chat_api):
    api = chat_api
    upload = api.client.post(f'/api/conversations/{api.conversation}/images',
                             files={'files': ('a.png', png(), 'image/png')}).json()['items'][0]
    body = {'image_ids': [upload['id']], 'document_ids': [api.doc1]}
    api.settings.llm_vision_enabled = False
    assert api.ask(body).json()['error']['code'] == 'VISION_NOT_READY'
    api.settings.llm_vision_enabled = True
    api.settings.image_max_context_bytes = 1
    answer = api.ask(body).json()['assistant_message']
    assert answer['status'] == 'failed' and answer['error']['code'] == 'IMAGE_BUDGET_EXCEEDED'
    assert not api.model_calls


def test_stale_pending_and_out_of_scope_search_results(chat_api, monkeypatch):
    api = chat_api
    def wrong_scope(_self, *_args, **_kwargs):
        return [dict(api.hit, document_id=api.doc2)]
    monkeypatch.setattr(ElasticsearchStore, 'search', wrong_scope)
    result = api.ask({'text': '购物车', 'document_ids': [api.doc1]}).json()
    assert result['assistant_message']['error']['code'] == 'INVALID_SEARCH_RESULT'
    assert not api.model_calls
    key = new_id()
    from app.repositories.chat import ChatRepository
    from app.schemas.chat import QuestionRequest
    body = QuestionRequest(text='后续问题')
    with api.factory() as session, session.begin():
        _, aid, _, _ = ChatRepository(session).reserve(api.conversation, key, body, api.settings)
    assert api.ask({'text': body.text}, key).json()['error']['code'] == 'REQUEST_IN_PROGRESS'
    with api.factory() as session, session.begin():
        session.get(Message, aid).updated_at = utcnow() - timedelta(hours=1)
    answer = api.ask({'text': body.text}, key).json()['assistant_message']
    assert answer['error']['code'] == 'REQUEST_INTERRUPTED'


def test_search_endpoint_and_mismatched_document_image(chat_api):
    api = chat_api
    result = api.client.post('/api/search', json={'text': '购物车', 'document_ids': [api.doc1]})
    assert result.status_code == 200 and result.json()[0]['source']['page'] == 2
    assert 'storage_key' not in result.text and str(api.settings.data_dir) not in result.text
    api.hit['page_image_keys'] = ['../../etc/passwd']
    result = api.ask({'text': '购物车', 'document_ids': [api.doc1]}).json()['assistant_message']
    assert result['error']['code'] == 'INVALID_SOURCE_IMAGE'


def test_image_pixels_and_paths_are_bounded(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, image_max_pixels=100)
    with pytest.raises(ApiError, match='') as error:
        decode_image(png((11, 10)), settings)
    assert error.value.code == 'IMAGE_TOO_LARGE'
    for key in ('../etc/passwd', '/etc/passwd', 'a\\b'):
        with pytest.raises(ApiError): resource_path(settings, key)


def test_unknown_citations_and_excessive_question_are_rejected(chat_api, monkeypatch):
    api = chat_api
    monkeypatch.setattr(LLMClient, 'complete', lambda *_args, **_kwargs: '100 件 [S1] [S0]')
    body = {'text': '购物车', 'document_ids': [api.doc1]}
    answer = api.ask(body).json()['assistant_message']
    assert answer['error']['code'] == 'INVALID_MODEL_CITATION'
    monkeypatch.setattr(LLMClient, 'complete', lambda *_args, **_kwargs: '未知答案，没有引用')
    answer = api.ask(body).json()['assistant_message']
    assert '依据不足' in answer['content'] and not answer['sources']
    answer = api.ask({'text': '问' * 15000, 'document_ids': [api.doc1]}).json()['assistant_message']
    assert answer['error']['code'] == 'CONTEXT_BUDGET_EXCEEDED'


def test_missing_document_image_slot_reports_budget_error(chat_api):
    api = chat_api
    api.settings.image_max_context_count = 4
    upload = api.client.post(f'/api/conversations/{api.conversation}/images', files=[
        ('files', (f'{i}.png', png(), 'image/png')) for i in range(4)])
    assert upload.status_code == 201
    body = {'text': '购物车', 'image_ids': [image['id'] for image in upload.json()['items']],
            'document_ids': [api.doc1]}
    answer = api.ask(body).json()['assistant_message']
    assert answer['error']['code'] == 'IMAGE_BUDGET_EXCEEDED'
    assert len(api.model_calls) == 1  # description only; no final request missing document images


def test_concurrent_mysql_sequence_and_image_registration(chat_api, request, monkeypatch):
    if request.node.callspec.params['chat_api'] != 'mysql':
        pytest.skip('Row lock concurrency requires the opted-in real MySQL fixture')
    from concurrent.futures import ThreadPoolExecutor
    api = chat_api
    monkeypatch.setattr(LLMClient, 'complete', lambda *_args, **_kwargs: '100 件 [S1]')
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: api.ask({'text': '购物车', 'document_ids': [api.doc1]}), range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert all(response.json()['assistant_message']['status'] == 'completed' for response in responses)
    messages = api.client.get(f'/api/conversations/{api.conversation}/messages').json()['items']
    assert [message['sequence'] for message in messages] == [1, 2, 3, 4]
    with api.factory() as session:
        assert len(session.scalars(select(ImageResource)).all()) == 1
