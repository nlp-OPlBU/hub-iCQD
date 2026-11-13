"""Versioned vector generations; deterministic writes double as durable checkpoints."""
import hashlib
import json
from app.services.embedding import EmbeddingClient
from app.services.elasticsearch_store import ElasticsearchStore, ElasticsearchError


class VectorIndex(ElasticsearchStore):
    @property
    def contract(self):
        return {'model': self.settings.embedding_model, 'dimensions': self.settings.embedding_dimensions,
                'encoding': 'qwen-vl-mean-normalized-v1'}

    def targets(self):
        return sorted(self._json('GET', '/_alias/' + self.settings.vector_write_alias))

    def check(self, index):
        mappings = self._json('GET', f'/{index}/_mapping')
        if len(mappings) != 1 or next(iter(mappings.values()))['mappings'].get('_meta') != self.contract:
            raise ElasticsearchError('Vector index model contract mismatch')

    def prepare(self, index):
        if not index.startswith(self.settings.vector_read_alias + '_'):
            raise ValueError('Generation must start with VECTOR_READ_ALIAS + underscore')
        configured = self.settings.model_copy(update={'es_index': index})
        ElasticsearchStore(configured).ensure_index()
        mapping = self._json('GET', f'/{index}/_mapping')[index]['mappings']
        if mapping.get('_meta') not in (None, self.contract):
            raise ElasticsearchError('Existing generation has different model')
        self._json('PUT', f'/{index}/_mapping', {'_meta': self.contract, 'properties': {
            'embedding': {'type': 'dense_vector', 'dims': self.settings.embedding_dimensions,
                          'index': True, 'similarity': 'cosine'},
            'vector_fingerprint': {'type': 'keyword'}}})
        self._json('POST', '/_aliases', {'actions': [{'add': {'index': index, 'alias': self.settings.vector_write_alias}}]})

    def fingerprint(self, chunk):
        return hashlib.sha256(json.dumps([self.contract, chunk], sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def missing(self, index, chunks):
        result = self._json('POST', f'/{index}/_mget?_source=vector_fingerprint', {'ids': [c['chunk_id'] for c in chunks]})
        existing = {d['_id']: d.get('_source', {}).get('vector_fingerprint') for d in result['docs']}
        return [c for c in chunks if existing.get(c['chunk_id']) != self.fingerprint(c)]

    def index_chunks(self, chunks, *, target=None):
        encoder = EmbeddingClient(self.settings)
        for index in ([target] if target else self.targets()):
            self.check(index)
            for chunk in self.missing(index, chunks):
                images = [encoder.image_part(key) for key in chunk.get('image_keys', [])]
                vector = encoder.encode(chunk['text'], images)
                record = dict(chunk, embedding=vector, vector_fingerprint=self.fingerprint(chunk))
                ElasticsearchStore(self.settings.model_copy(update={'es_index': index})).index_chunks([record])

    def search_vector(self, vector, versions, limit):
        if not versions:
            return []
        self.check(self.settings.vector_read_alias)
        scope = [{'bool': {'filter': [{'term': {'document_id': doc}}, {'term': {'version': version}}]}}
                 for doc, version in versions.items()]
        result = self._json('POST', f'/{self.settings.vector_read_alias}/_search', {
            'size': limit, '_source': {'excludes': ['embedding', 'vector_fingerprint']},
            'knn': {'field': 'embedding', 'query_vector': vector, 'k': limit,
                    'num_candidates': max(100, limit),
                    'filter': {'bool': {'should': scope, 'minimum_should_match': 1}}}})
        if result.get('timed_out') or result.get('_shards', {}).get('failed', 0):
            raise ElasticsearchError('Incomplete vector search')
        return [dict(h['_source'], score=h['_score']) for h in result['hits']['hits']]

    def activate(self, index):
        self.check(index)
        if index not in self.targets():
            raise ElasticsearchError('Generation is not maintained by workers')
        status, body = self._request('GET', '/_alias/' + self.settings.vector_read_alias)
        if status not in (200, 404):
            raise ElasticsearchError('Cannot read active alias')
        previous = list(json.loads(body)) if status == 200 else []
        actions = [{'remove': {'index': old, 'alias': self.settings.vector_read_alias}} for old in previous]
        actions.append({'add': {'index': index, 'alias': self.settings.vector_read_alias}})
        self._json('POST', '/_aliases', {'actions': actions})
        return previous
