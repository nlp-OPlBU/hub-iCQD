import json

import pytest

from app.core.config import Settings
from app.services.elasticsearch_store import ElasticsearchError, ElasticsearchStore


@pytest.mark.parametrize('response', [
    {'errors': True, 'items': [{'index': {'status': 429}}]},
    {'errors': False, 'items': []},
])
def test_bulk_item_failure_is_not_success(monkeypatch, response):
    store = ElasticsearchStore(Settings(_env_file=None))
    monkeypatch.setattr(store, 'ensure_index', lambda: None)
    monkeypatch.setattr(store, '_request', lambda *a, **k: (200, json.dumps(response).encode()))
    with pytest.raises(ElasticsearchError):
        store.index_chunks([{'chunk_id': 'd:1:0'}])


def test_search_visibility_required(monkeypatch):
    store = ElasticsearchStore(Settings(_env_file=None))
    monkeypatch.setattr(store, 'ensure_index', lambda: None)
    monkeypatch.setattr(store, '_request', lambda *a, **k: (200, b'{"errors":false,"items":[{"index":{"status":201}}]}'))
    monkeypatch.setattr(store, '_json', lambda *a: {'hits': {'total': {'value': 0}}})
    with pytest.raises(ElasticsearchError, match='searchable'):
        store.index_chunks([{'chunk_id': 'd:1:0'}])


def test_bad_index_mapping_is_rejected(monkeypatch):
    store = ElasticsearchStore(Settings(_env_file=None))
    responses = iter([(404, b''), (400, b'{"error":{"type":"mapper_parsing_exception"}}')])
    monkeypatch.setattr(store, '_request', lambda *a: next(responses))
    with pytest.raises(ElasticsearchError):
        store.ensure_index()


def test_bm25_scope_is_document_version_pairs(monkeypatch):
    store = ElasticsearchStore(Settings(_env_file=None))
    calls = []
    def response(*args):
        calls.append(args[2])
        return {'hits': {'hits': []}}
    monkeypatch.setattr(store, '_json', response)
    assert store.search('商品', versions={}) == [] and not calls
    store.search('商品', versions={'doc': 2})
    scope = calls[0]['query']['bool']['filter'][0]['bool']
    assert scope['minimum_should_match'] == 1
    assert scope['should'][0]['bool']['filter'] == [
        {'term': {'document_id': 'doc'}}, {'term': {'version': 2}}]
