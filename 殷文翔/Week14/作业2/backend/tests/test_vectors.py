import math
import pytest
from app.core.config import Settings
from app.services.embedding import EmbeddingClient
from app.services.elasticsearch_store import ElasticsearchError, ElasticsearchStore
from app.services.vector_index import VectorIndex
from app.services.retrieval import fuse, search_snapshot


def hit(doc='d', version=1, chunk='a'):
    return dict(document_id=doc, version=version, chunk_id=chunk, text='evidence', page=1)


def test_rrf_deduplicates_and_rewards_both_routes():
    result = fuse([[hit(chunk='a'), hit(chunk='a'), hit(chunk='b')], [hit(chunk='b')]])
    assert [h['chunk_id'] for h in result] == ['b', 'a']
    assert result[1]['score'] == 1/61


def test_multimodal_mean_is_normalized(monkeypatch):
    client = EmbeddingClient(Settings(_env_file=None))
    monkeypatch.setattr(client, '_one', lambda text, image: image)
    assert client.encode('x', [[1, 0], [0, 1]]) == pytest.approx([1/math.sqrt(2)]*2)
    with pytest.raises(ElasticsearchError):
        client.encode('x', [[0, 0]])


def test_vector_scope_and_source_exclusion(monkeypatch):
    store = VectorIndex(Settings(_env_file=None))
    calls = []
    monkeypatch.setattr(store, 'check', lambda _: None)
    monkeypatch.setattr(store, '_json', lambda *args: calls.append(args[2]) or {'hits': {'hits': []}})
    store.search_vector([1, 0], {'d': 2, 'other': 1}, 10)
    filters = calls[0]['knn']['filter']['bool']
    assert filters['minimum_should_match'] == 1
    assert filters['should'][0]['bool']['filter'] == [
        {'term': {'document_id': 'd'}}, {'term': {'version': 2}}]
    assert 'embedding' in calls[0]['_source']['excludes']


def test_hybrid_rejects_stale_vector_result(monkeypatch):
    settings = Settings(_env_file=None, retrieval_mode='hybrid')
    store = ElasticsearchStore(settings)
    monkeypatch.setattr(store, 'search', lambda *a, **k: [hit()])
    monkeypatch.setattr(EmbeddingClient, 'encode', lambda *a: [1, 0])
    monkeypatch.setattr(VectorIndex, 'search_vector', lambda *a: [hit(version=2)])
    with pytest.raises(Exception, match='检索来源校验失败'):
        search_snapshot(store, 'query', {'d': 1}, 10)


def test_checkpoint_retries_only_missing_or_changed(monkeypatch):
    store = VectorIndex(Settings(_env_file=None))
    chunks = [hit(chunk='a'), hit(chunk='b')]
    monkeypatch.setattr(store, '_json', lambda *a: {'docs': [
        {'_id': 'a', '_source': {'vector_fingerprint': store.fingerprint(chunks[0])}},
        {'_id': 'b', 'found': False}]})
    assert store.missing('candidate', chunks) == [chunks[1]]
    changed = dict(chunks[0], text='changed')
    assert store.missing('candidate', [changed]) == [changed]


def test_switch_is_atomic_and_preserves_prior_generation(monkeypatch):
    store = VectorIndex(Settings(_env_file=None))
    monkeypatch.setattr(store, 'check', lambda _: None)
    monkeypatch.setattr(store, 'targets', lambda: ['new', 'old'])
    monkeypatch.setattr(store, '_request', lambda *a: (200, b'{"old":{}}'))
    calls = []
    monkeypatch.setattr(store, '_json', lambda *a: calls.append(a))
    assert store.activate('new') == ['old']
    actions = calls[0][2]['actions']
    assert [list(a)[0] for a in actions] == ['remove', 'add']
    assert 'remove_index' not in str(actions)


@pytest.mark.parametrize('vector', [[1], [0, float('nan')], [True, 1]])
def test_embedding_rejects_malformed_service_vectors(monkeypatch, vector):
    import httpx
    settings = Settings(_env_file=None, embedding_dimensions=2)
    def post(*args, **kwargs):
        # A decoded malformed response also covers NaN, which valid JSON forbids.
        class Response:
            def raise_for_status(self):
                pass
            def json(self):
                return {'data': [{'embedding': vector}]}
        return Response()
    monkeypatch.setattr(httpx.Client, 'post', post)
    with pytest.raises(ElasticsearchError):
        EmbeddingClient(settings).encode('query')


def test_generation_rejects_wrong_model_contract(monkeypatch):
    store = VectorIndex(Settings(_env_file=None))
    monkeypatch.setattr(store, '_json', lambda *a: {'index': {'mappings': {'_meta': {'model': 'other'}}}})
    with pytest.raises(ElasticsearchError, match='contract'):
        store.check('index')
