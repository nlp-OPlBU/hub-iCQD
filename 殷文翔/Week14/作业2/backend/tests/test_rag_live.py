"""Opt-in real MySQL + ES + local vision model acceptance, with existing MinerU output.

RAG_TEST_MYSQL_BINARY enables the isolated MySQL fixture. Additionally set
RAG_LIVE_ES_URL, RAG_LIVE_LLM_URL and RAG_LIVE_ARTIFACT_DIR. Never starts shared services.
"""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import shutil

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import httpx
from PIL import Image
import pytest
from sqlalchemy import select, text

from app.core.config import PROJECT_ROOT, Settings
from app.db.base import new_id
from app.db.session import create_db_engine, session_factory
from app.main import create_app
from app.models import Document, Message, ParseTask
from app.services.chunking import build_chunks
from app.services.elasticsearch_store import ElasticsearchStore


@pytest.mark.skipif(not all(os.environ.get(key) for key in (
    'RAG_LIVE_ES_URL', 'RAG_LIVE_LLM_URL', 'RAG_LIVE_ARTIFACT_DIR')), reason='Explicit real ES/model/artifact opt-in required')
def test_live_scoped_text_then_image_question_with_sources(isolated_mysql, monkeypatch):
    root_engine, mysql = isolated_mysql
    run_id = new_id()
    directory = PROJECT_ROOT / 'runtime' / 'rag-acceptance'
    directory.mkdir(parents=True, exist_ok=True)
    database = 'rag_live_' + run_id.replace('-', '')
    with root_engine.begin() as connection:
        connection.execute(text(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4'))
    settings = Settings(_env_file=None, mysql_host=mysql.mysql_host, mysql_port=mysql.mysql_port,
        mysql_user=mysql.mysql_user, mysql_password=mysql.mysql_password, mysql_database=database,
        data_dir=directory / 'data', es_url=os.environ['RAG_LIVE_ES_URL'],
        es_index='multimodal-rag-live-' + run_id, llm_base_url=os.environ['RAG_LIVE_LLM_URL'],
        llm_vision_enabled=False, image_max_context_count=4, retrieval_top_k=2)
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    config = Config(str(PROJECT_ROOT / 'backend' / 'alembic.ini'))
    with engine.connect() as connection:
        config.attributes['connection'] = connection
        command.upgrade(config, 'head'); connection.commit()
    original = Path(os.environ['RAG_LIVE_ARTIFACT_DIR']).resolve()
    source_artifact = json.loads((original / 'normalized.json').read_text())
    # The archived source is unmodified. Copy it under this test's fresh document identity.
    doc1, doc2 = new_id(), new_id()
    key = f'parsed/{doc1}/v1/acceptance'
    copied = settings.data_dir / key
    copied.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(original, copied)
    old_prefix = next(image for page in source_artifact['pages'] for block in page['blocks']
                      for image in block['image_keys']).split('/images/')[0]
    artifact = json.loads(json.dumps(source_artifact).replace(old_prefix + '/', key + '/'))
    (copied / 'normalized.json').write_text(json.dumps(artifact, ensure_ascii=False), encoding='utf-8')
    with factory() as session, session.begin():
        for doc, filename in ((doc1, 'test2.pdf'), (doc2, '范围隔离对照.pdf')):
            session.add(Document(id=doc, original_filename=filename, storage_key='uploads/' + doc,
                                 sha256='a' * 64, size_bytes=1))
        session.flush()
        for doc in (doc1, doc2):
            session.add(ParseTask(id=new_id(), document_id=doc, version=1, status='completed',
                max_retries=0, artifact_key=key if doc == doc1 else None, retrieval_mode='bm25',
                index_name=settings.es_index))
        session.flush()
        for doc in (doc1, doc2): session.get(Document, doc).current_searchable_version = 1
    store = ElasticsearchStore(settings)
    chunks = build_chunks(document_id=doc1, version=1, filename='test2.pdf', artifact=artifact)
    control = {'document_id': doc2, 'version': 1, 'filename': '范围隔离对照.pdf', 'page': 1,
               'chunk_id': f'{doc2}:1:1:0:0', 'text': '购物车最多可以添加999件商品。',
               'image_keys': [], 'page_image_keys': []}
    store.index_chunks([*chunks, control])
    outbound = []
    original_post = httpx.Client.post
    def capture(self, url, *args, **kwargs):
        body = kwargs.get('json')
        if str(url).startswith(settings.llm_base_url) and body:
            images = []
            for message in body['messages']:
                if isinstance(message['content'], list):
                    for part in message['content']:
                        if part['type'] == 'image_url':
                            data = base64.b64decode(part['image_url']['url'].split(',', 1)[1])
                            with Image.open(io.BytesIO(data)) as image:
                                images.append({'bytes': len(data), 'width': image.width,
                                    'height': image.height, 'sha256': hashlib.sha256(data).hexdigest()})
            outbound.append({'system': body['messages'][0]['content'], 'images': images,
                             'model': body['model']})
        return original_post(self, url, *args, **kwargs)
    monkeypatch.setattr(httpx.Client, 'post', capture)
    report = {'run_id': run_id, 'doc1': doc1, 'doc2': doc2, 'es_index': settings.es_index}
    try:
        with TestClient(create_app(settings)) as client:
            conversation = client.post('/api/conversations', json={'title': '真实图文验收'}).json()['id']
            endpoint = f'/api/conversations/{conversation}/messages'
            def ask(payload, key=None):
                response = client.post(endpoint, json=payload, headers={'Idempotency-Key': key or new_id()})
                assert response.status_code == 200, response.text
                result = response.json()
                assert result['assistant_message']['status'] == 'completed', result
                return result
            plain = ask({'text': '购物车最多可以添加多少件商品？', 'document_ids': [doc1]})
            report['text_answer'] = plain
            assert '100' in plain['assistant_message']['content']
            assert all(source['document_id'] == doc1 for source in plain['assistant_message']['sources'])
            scoped = ask({'text': '购物车最多可以添加多少件商品？', 'document_ids': [doc2]})
            report['scope_control_answer'] = scoped
            assert '999' in scoped['assistant_message']['content']
            assert all(source['document_id'] == doc2 for source in scoped['assistant_message']['sources'])
            insufficient = ask({'text': 'ZZZ_NO_EVIDENCE_9867123', 'document_ids': [doc1]})
            assert insufficient['assistant_message']['sources'] == []
            assert '依据不足' in insufficient['assistant_message']['content']
            settings.llm_vision_enabled = True
            image_path = copied / 'images' / 'page_0_image_5.jpg'
            upload = client.post(f'/api/conversations/{conversation}/images', files={
                'files': ('question.jpg', image_path.read_bytes(), 'image/jpeg')})
            assert upload.status_code == 201, upload.text
            image = upload.json()['items'][0]
            key = new_id()
            body = {'text': '图中底部流程中间的方框内写着什么？购物车最多可以添加多少件商品？',
                    'image_ids': [image['id']], 'document_ids': [doc1]}
            multimodal = ask(body, key)
            report['multimodal_answer'] = multimodal
            assert '100' in multimodal['assistant_message']['content']
            assert '问答技能' in multimodal['assistant_message']['content']
            before = len(outbound)
            assert ask(body, key) == multimodal and len(outbound) == before
            image_only = ask({'image_ids': [image['id']], 'document_ids': [doc1]})
            report['image_only_answer'] = image_only
            assert image_only['assistant_message']['sources']
            for answer in (plain, multimodal, image_only):
                for source in answer['assistant_message']['sources']:
                    actual = next(chunk for chunk in chunks if chunk['chunk_id'] == source['chunk_id'])
                    assert actual['page'] == source['page'] and source['document_id'] == doc1
                    for resource in source['images']:
                        metadata = client.get('/api/images/' + resource['id']).json()
                        assert metadata['page'] == source['page'] and metadata['document_id'] == doc1
                        assert client.get(resource['url']).status_code == 200
            # Two text calls, then description + final for both image questions.
            assert [len(call['images']) for call in outbound[:2]] == [0, 0]
            assert len(outbound[2]['images']) == 1 and len(outbound[3]['images']) >= 2
            assert len(outbound[4]['images']) == 1 and len(outbound[5]['images']) >= 2
            messages = client.get(endpoint + '?page_size=100').json()['items']
            assert len(messages) == 10
            with factory() as session:
                stored = session.get(Message, multimodal['assistant_message']['id'])
                assert stored.sources == multimodal['assistant_message']['sources']
                assert stored.document_scope == [{'document_id': doc1, 'version': 1}]
            report.update(conversation_id=conversation, persisted_message_count=len(messages),
                          real_outbound_model_requests=outbound, outcome='passed')
    finally:
        (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        store._request('DELETE', '/' + settings.es_index)
        engine.dispose()
        with root_engine.begin() as connection:
            connection.execute(text(f'DROP DATABASE `{database}`'))
