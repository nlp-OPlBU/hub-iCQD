"""Provenance acceptance: multipage evidence, search/chat citations and PDF viewing."""
import io
import json
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest
from sqlalchemy.orm import Session

from app.models import Document, ParseTask
from app.services.chunking import build_chunks
from app.services.elasticsearch_store import ElasticsearchStore
from app.services.llm_client import LLMClient
from app.services.mineru_artifacts import load_artifacts
from app.services.mineru_cli import MineruError
from tests.test_documents import PDF, upload_app  # Reuse isolated SQLite API, never shared services.


def write_input(directory):
    directory.mkdir(parents=True, exist_ok=True)
    stream = io.BytesIO()
    Image.new('RGB', (40, 30), 'blue').save(stream, format='PNG')
    for name in ('figure.png', 'table.png', 'cell.png', 'next.png'):
        (directory / name).write_bytes(stream.getvalue())
    (directory / 'markdown.md').write_text('原始正文\n表格与图片', encoding='utf-8')
    payload = {'pages': [
        {'page_idx': 0, 'blocks': [
            {'type': 'paragraph_title', 'content': '销售报告', 'bbox': [0.1, 0.1, 0.9, 0.2]},
            {'type': 'text', 'content': '本页销售数据。' * 200, 'bbox': [0.1, 0.2, 0.9, 0.3]},
            {'type': 'image', 'content': '', 'image_source': 'figure.png', 'bbox': [0.1, 0.3, 0.8, 0.5]},
            {'type': 'table', 'content': '<table><tr><th>商品</th><th>销量</th></tr>'
                '<tr><td>苹果</td><td>100<img src="cell.png" /></td></tr></table>',
             'image_source': 'table.png', 'captions': [{'content': '表1 销量'}],
             'footnotes': ['单位：件'], 'bbox': [0.1, 0.5, 0.9, 0.8]},
        ]},
        {'page_idx': 1, 'blocks': [
            {'type': 'image', 'content': '', 'image_source': 'next.png',
             'captions': ['次页对比图'], 'bbox': [0.1, 0.2, 0.9, 0.8]},
        ]},
    ]}
    (directory / 'structured_content.json').write_text(json.dumps(payload), encoding='utf-8')
    return payload


@pytest.fixture
def evidence_api(upload_app):
    client, app, engine, settings = upload_app
    doc = client.post('/api/documents/batch', files=[('files', ('销售报告.pdf', PDF, 'application/pdf'))]).json()['results'][0]['document']
    key = f'parsed/{doc["id"]}/v1/test'
    directory = settings.data_dir / key
    write_input(directory)
    artifact = load_artifacts(directory, key)
    artifact.update(document_id=doc['id'], version=1)
    (directory / 'normalized.json').write_text(json.dumps(artifact), encoding='utf-8')
    with Session(engine) as session, session.begin():
        task = session.get(ParseTask, doc['latest_task']['id'])
        task.status = 'completed'; task.artifact_key = key; task.index_name = settings.es_index
        session.get(Document, doc['id']).current_searchable_version = 1
    chunks = build_chunks(document_id=doc['id'], version=1, filename='销售报告.pdf', artifact=artifact)
    return SimpleNamespace(client=client, app=app, engine=engine, settings=settings, id=doc['id'],
                           directory=directory, artifact=artifact, chunks=chunks)


def test_multipage_evidence_is_bounded_and_reconstructs_each_original_block(evidence_api):
    api = evidence_api
    chunks = build_chunks(document_id=api.id, version=1, filename='销售报告.pdf', artifact=api.artifact, max_chars=15)
    assert chunks == build_chunks(document_id=api.id, version=1, filename='销售报告.pdf', artifact=api.artifact, max_chars=15)
    for page in api.artifact['pages']:
        for block in page['blocks']:
            selected = [c for c in chunks if c['page'] == page['page'] and c['block_index'] == block['block_index']]
            assert ''.join(c['text'] for c in selected) == block['text']
            for chunk in selected:
                assert 0 < len(chunk['text']) <= 15
                assert chunk['text'] == block['text'][chunk['char_offset']:chunk['char_end']]
                assert chunk['bbox'] == block['bbox']
                assert chunk['image_keys'] == block['image_keys']
    table = api.artifact['pages'][0]['blocks'][3]
    assert table['content'].startswith('<table>') and table['table_rows'] == ['商品 | 销量', '苹果 | 100']
    assert table['text'] == '商品 | 销量\n苹果 | 100\n表1 销量\n单位：件'
    assert len(table['image_keys']) == 2
    table_chunks = [c for c in chunks if c['evidence_type'] == 'table']
    assert table_chunks[0]['text'] == '商品 | 销量\n'
    assert all('next.png' not in key for c in chunks if c['page'] == 1 for key in c['page_image_keys'])
    image = next(c for c in chunks if c['evidence_type'] == 'image' and c['page'] == 1)
    assert image['text_origin'] == 'page_context'
    assert '销售数据' in api.artifact['pages'][0]['blocks'][2]['text']


def test_pdf_inline_range_versions_missing_files_and_page_evidence(evidence_api):
    api = evidence_api
    url = f'/api/documents/{api.id}'
    pdf = api.client.get(url + '/content?version=1')
    assert pdf.status_code == 200 and pdf.content == PDF
    assert pdf.headers['content-type'] == 'application/pdf'
    assert pdf.headers['content-disposition'].startswith('inline;')
    assert pdf.headers['x-content-type-options'] == 'nosniff'
    partial = api.client.get(url + '/content?version=1', headers={'Range': 'bytes=0-7'})
    assert partial.status_code == 206 and partial.content == PDF[:8]
    assert api.client.get(url + '/content?version=99').status_code == 404
    assert api.client.get(url + '/content?version=0').status_code == 422
    assert api.client.get(f'/api/documents/{uuid4()}/content').status_code == 404
    page = api.client.get(url + '/pages/1?version=1').json()
    assert page['page_count'] == 2 and page['version'] == 1
    table = page['blocks'][3]
    assert table['evidence_type'] == 'table' and len(table['images']) == 2
    assert all(image['page'] == 1 and image['document_id'] == api.id for image in table['images'])
    assert all(api.client.get(image['url']).status_code == 200 for image in table['images'])
    assert 'storage_key' not in json.dumps(page) and str(api.settings.data_dir) not in json.dumps(page)
    assert api.client.get(url + '/pages/3?version=1').status_code == 404
    assert api.client.get(url + '/pages/0?version=1').status_code == 422
    (api.settings.data_dir / f'uploads/{api.id}.pdf').unlink()
    assert api.client.get(url + '/content').json()['error']['code'] == 'DOCUMENT_FILE_UNAVAILABLE'


def test_search_answer_and_history_share_precise_table_source(evidence_api, monkeypatch):
    api = evidence_api
    table = next(c for c in api.chunks if c['evidence_type'] == 'table')
    monkeypatch.setattr(ElasticsearchStore, 'search', lambda *args, **kwargs: [dict(table, score=1.2)])
    monkeypatch.setattr(LLMClient, 'complete', lambda *args, **kwargs: '苹果销量为100件。[S1]')
    source = api.client.post('/api/search', json={'text': '销量', 'document_ids': [api.id]}).json()[0]['source']
    assert (source['evidence_type'], source['page'], source['block_index']) == ('table', 1, 3)
    assert source['image_relation'] == 'block' and len(source['images']) == 2
    assert source['char_offset'] == 0 and source['char_end'] == len(table['text'])
    conversation = api.client.post('/api/conversations', json={}).json()['id']
    messages_url = f'/api/conversations/{conversation}/messages'
    answer = api.client.post(messages_url, json={'text': '苹果销量', 'document_ids': [api.id]},
                             headers={'Idempotency-Key': str(uuid4())}).json()['assistant_message']
    assert answer['status'] == 'completed' and answer['sources'] == [source]
    assert api.client.get(messages_url).json()['items'][1]['sources'] == [source]
    # Old citations keep their immutable version even after a newer task is current.
    with Session(api.engine) as session, session.begin():
        session.add(ParseTask(id=str(uuid4()), document_id=api.id, version=2, status='pending', max_retries=0))
        session.get(Document, api.id).latest_version = 2
    assert api.client.get(f'/api/documents/{api.id}/pages/1?version=1').json()['blocks'][3]['text'] == table['text']
    assert api.client.get(f'/api/documents/{api.id}/pages/1?version=2').json()['error']['code'] == 'DOCUMENT_NOT_PARSED'


@pytest.mark.parametrize('field,value', [('page', 2), ('block_index', 0), ('char_offset', 1),
    ('bbox', [0, 0, 1, 1]), ('image_keys', []), ('text', '伪造内容')])
def test_mismatched_source_position_is_rejected(evidence_api, monkeypatch, field, value):
    api = evidence_api
    table = dict(next(c for c in api.chunks if c['evidence_type'] == 'table'), score=1.0)
    table[field] = value
    monkeypatch.setattr(ElasticsearchStore, 'search', lambda *args, **kwargs: [table])
    result = api.client.post('/api/search', json={'text': '销量'})
    assert result.status_code == 503 and result.json()['error']['code'] == 'INVALID_SOURCE_POSITION'


@pytest.mark.parametrize('reference', ['../secret.png', '/etc/passwd', 'missing.png'])
def test_table_embedded_image_must_resolve_safely_on_its_page(tmp_path, reference):
    payload = write_input(tmp_path)
    payload['pages'][0]['blocks'][3]['content'] = f'<table><tr><td><img src="{reference}"></td></tr></table>'
    (tmp_path / 'structured_content.json').write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(MineruError) as error:
        load_artifacts(tmp_path, 'parsed/d/v1')
    assert error.value.code == 'ARTIFACT_INVALID'


@pytest.mark.parametrize('bbox', [[0, 0, 1], [0, 0, float('nan'), 1], [0.9, 0, 0.1, 1]])
def test_invalid_block_location_fails_parsing(tmp_path, bbox):
    payload = write_input(tmp_path)
    payload['pages'][0]['blocks'][0]['bbox'] = bbox
    (tmp_path / 'structured_content.json').write_text(json.dumps(payload), encoding='utf-8')
    with pytest.raises(MineruError):
        load_artifacts(tmp_path, 'parsed/d/v1')


def test_new_artifact_cannot_bypass_position_check_by_omitting_type(evidence_api, monkeypatch):
    api = evidence_api
    table = dict(next(c for c in api.chunks if c['evidence_type'] == 'table'), score=1.0)
    del table['evidence_type']
    monkeypatch.setattr(ElasticsearchStore, 'search', lambda *args, **kwargs: [table])
    result = api.client.post('/api/search', json={'text': '销量'})
    assert result.status_code == 503 and result.json()['error']['code'] == 'INVALID_SOURCE_POSITION'


def test_artifact_version_mismatch_is_not_exposed(evidence_api):
    api = evidence_api
    artifact = dict(api.artifact, version=99)
    (api.directory / 'normalized.json').write_text(json.dumps(artifact), encoding='utf-8')
    result = api.client.get(f'/api/documents/{api.id}/pages/1?version=1')
    assert result.status_code == 503 and result.json()['error']['code'] == 'SOURCE_UNAVAILABLE'


def test_image_only_document_keeps_searchable_image_evidence(tmp_path):
    payload = write_input(tmp_path)
    payload['pages'] = [{'page_idx': 0, 'blocks': [{'type': 'image', 'content': '', 'image_source': 'figure.png'}]}]
    (tmp_path / 'structured_content.json').write_text(json.dumps(payload), encoding='utf-8')
    artifact = load_artifacts(tmp_path, 'parsed/d/v1')
    chunks = build_chunks(document_id='d', version=1, filename='scan.pdf', artifact=artifact)
    assert len(chunks) == 1 and chunks[0]['evidence_type'] == 'image'
    assert chunks[0]['image_keys'] == ['parsed/d/v1/figure.png']
    assert chunks[0]['text_origin'] == 'page_context' and '无文字说明' in chunks[0]['text']
