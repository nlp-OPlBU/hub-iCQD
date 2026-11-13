import json

import httpx
import pytest

from app.core.config import Settings
from app.services.reranker import RerankerClient, rerank
from app.services.elasticsearch_store import ElasticsearchStore
from tests.test_rag import chat_api, model_server, png


def score_transport(monkeypatch, responder):
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(responder)))


def test_multimodal_payload_and_response_indices(monkeypatch):
    image = {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,aGVsbG8='}}
    documents = [{'content': [{'type': 'text', 'text': '无关'}]}, {'content': [image]}]
    def respond(request):
        body = json.loads(request.content)
        assert body['queries']['content'][-1] == image
        assert body['documents'] == documents
        return httpx.Response(200, json={'model': 'rag-reranker', 'data': [
            {'index': 1, 'score': .9}, {'index': 0, 'score': .1}]})
    score_transport(monkeypatch, respond)
    hits, warning = rerank(Settings(_env_file=None), '图里的内容',
        [{'chunk_id': 'a', 'score': 2}, {'chunk_id': 'b', 'score': 1}], documents, [image])
    assert warning is None and [h['chunk_id'] for h in hits] == ['b', 'a']
    assert hits[0]['retrieval_score'] == 1 and hits[0]['rerank_score'] == .9


@pytest.mark.parametrize('data', [[], [{'index': 0, 'score': .5}],
    [{'index': 0, 'score': .5}, {'index': 0, 'score': .7}],
    [{'index': 0, 'score': .5}, {'index': 2, 'score': .7}],
    [{'index': 0, 'score': .5}, {'index': 1, 'score': '0.7'}],
    [{'index': 0, 'score': .5}, {'index': 1, 'score': 1.7}],
    [{'index': 0, 'score': .5}, {'index': True, 'score': .7}]])
def test_invalid_partial_scores_preserve_original_order(monkeypatch, data):
    score_transport(monkeypatch, lambda _: httpx.Response(200, json={'model': 'rag-reranker', 'data': data}))
    original = [{'chunk_id': 'a', 'score': 2}, {'chunk_id': 'b', 'score': 1}]
    hits, warning = rerank(Settings(_env_file=None), '问题', original, ['a', 'b'])
    assert '降级' in warning and [h['chunk_id'] for h in hits] == ['a', 'b']
    assert [h['score'] for h in hits] == [2, 1]
    assert all(h['rerank_status'] == 'degraded' and 'rerank_score' not in h for h in hits)
    assert 'rerank_status' not in original[0]


@pytest.mark.parametrize('failure', ['timeout', 'http', 'nan', 'model', 'late_batch'])
def test_failures_and_nonfinite_scores_degrade_whole_candidate_set(monkeypatch, failure):
    calls = []
    def respond(request):
        calls.append(request)
        if failure == 'timeout':
            raise httpx.ReadTimeout('untrusted response')
        if failure == 'http' or (failure == 'late_batch' and len(calls) > 1):
            return httpx.Response(503)
        if failure == 'nan':
            return httpx.Response(200, content=b'{"model":"rag-reranker","data":[{"index":0,"score":NaN}]}')
        return httpx.Response(200, json={'model': 'wrong' if failure == 'model' else 'rag-reranker',
                                       'data': [{'index': 0, 'score': .8}]})
    score_transport(monkeypatch, respond)
    hits, warning = rerank(Settings(_env_file=None, rerank_batch_size=1), '问题',
                          [{'score': 2}, {'score': 1}], ['a', 'b'])
    assert warning and [h['score'] for h in hits] == [2, 1]


def test_rag_reranks_beyond_top_k_with_actual_images_and_persists_sources(chat_api, monkeypatch):
    api = chat_api
    api.settings.rerank_enabled = True
    api.settings.retrieval_top_k = 1
    calls = []
    def search(_self, query, *, versions, limit):
        assert limit == api.settings.retrieval_candidates
        return [dict(api.hit, chunk_id='less-relevant', text='无关文本'), api.hit]
    monkeypatch.setattr(ElasticsearchStore, 'search', search)
    def score(self, query, documents, images=None):
        calls.append(documents)
        assert len(documents) == 2
        assert documents[1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        assert images[0]['image_url']['url'].startswith('data:image/jpeg;base64,')
        return [.01, .99]
    monkeypatch.setattr(RerankerClient, 'score', score)
    image = api.client.post(f'/api/conversations/{api.conversation}/images',
        files={'files': ('original.png', png(), 'image/png')}).json()['items'][0]
    answer = api.ask({'text': '购物车数量', 'image_ids': [image['id']], 'document_ids': [api.doc1]}).json()['assistant_message']
    assert answer['status'] == 'completed', answer
    assert answer['sources'][0]['chunk_id'] == api.hit['chunk_id']
    assert answer['sources'][0]['rerank_score'] == .99
    parts = api.model_calls[-1]['messages'][1]['content']
    assert len([p for p in parts if p['type'] == 'image_url']) == 2
    assert len(calls) == 1


def test_rag_and_search_expose_degradation_and_history_preserves_it(chat_api, monkeypatch):
    from app.services.reranker import RerankError
    api = chat_api
    api.settings.rerank_enabled = True
    def failure(*args, **kwargs):
        raise RerankError('timeout')
    monkeypatch.setattr(RerankerClient, 'score', failure)
    body = {'text': '购物车', 'document_ids': [api.doc1]}
    search = api.client.post('/api/search', json=body).json()[0]
    assert search['rerank_status'] == 'degraded' and '降级' in search['warning']
    answer = api.ask(body).json()['assistant_message']
    assert answer['status'] == 'completed' and '降级' in answer['content']
    assert answer['sources'][0]['rerank_score'] is None
    history = api.client.get(f'/api/conversations/{api.conversation}/messages').json()['items']
    assert history[-1]['content'] == answer['content']


def test_invalid_provenance_never_reaches_reranker(chat_api, monkeypatch):
    api = chat_api
    api.settings.rerank_enabled = True
    api.hit['page_image_keys'] = ['../../etc/passwd']
    monkeypatch.setattr(RerankerClient, 'score', lambda *a, **k: pytest.fail('Invalid image sent'))
    answer = api.ask({'text': '购物车'}).json()['assistant_message']
    assert answer['error']['code'] == 'INVALID_SOURCE_IMAGE'


def test_explicit_insufficiency_does_not_present_irrelevant_sources(chat_api, monkeypatch):
    from app.services.llm_client import LLMClient
    monkeypatch.setattr(LLMClient, 'complete', lambda *a, **k: '依据不足。来源 [S1] 与问题无关。')
    answer = chat_api.ask({'text': '火星银行利润', 'document_ids': [chat_api.doc1]}).json()['assistant_message']
    assert answer['status'] == 'completed' and '依据不足' in answer['content']
    assert answer['sources'] == [] and '/api/images/' not in answer['content']
