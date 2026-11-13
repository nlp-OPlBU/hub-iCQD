"""BM25 retrieval limited to MySQL's completed, current document versions."""
from app.repositories.document import DocumentRepository
from app.services.elasticsearch_store import ElasticsearchStore
from app.api.errors import ApiError


def document_scope(repository: DocumentRepository, document_ids: list[str] | None, *, index_name=None) -> dict[str, int]:
    versions = repository.searchable_versions(document_ids, index_name=index_name)
    if document_ids is not None and (not document_ids or set(versions) != set(document_ids)):
        raise ApiError(422, 'INVALID_DOCUMENT_SCOPE', '指定范围包含不存在或不可检索的文档')
    return versions


def search_snapshot(store: ElasticsearchStore, query: str, versions: dict[str, int], limit: int, *, images=None):
    if store.settings.retrieval_mode == 'hybrid' and versions:
        from concurrent.futures import ThreadPoolExecutor
        from app.services.embedding import EmbeddingClient
        from app.services.vector_index import VectorIndex
        count = max(limit, store.settings.retrieval_candidates)
        def semantic():
            vector = EmbeddingClient(store.settings).encode(query, images)
            return VectorIndex(store.settings).search_vector(vector, versions, count)
        with ThreadPoolExecutor(max_workers=2) as pool:
            lexical = pool.submit(store.search, query, versions=versions, limit=count)
            dense = pool.submit(semantic)
            hits = fuse([lexical.result(), dense.result()], store.settings.retrieval_rrf_k)
    else:
        hits = store.search(query, versions=versions, limit=limit)
    # Defense in depth: never expose an out-of-scope/stale hit, even with a bad ES response.
    for hit in hits:
        if (versions.get(hit.get('document_id')) != hit.get('version') or not hit.get('chunk_id')
                or not isinstance(hit.get('text'), str) or not hit['text'].strip()
                or (hit.get('page') is not None and
                    (type(hit['page']) is not int or hit['page'] < 1))):
            raise ApiError(503, 'INVALID_SEARCH_RESULT', '检索来源校验失败')
    return hits[:limit]


def retrieve(repository: DocumentRepository, store: ElasticsearchStore, query: str,
             *, document_ids: list[str] | None = None, limit: int = 10) -> list[dict]:
    return search_snapshot(store, query, document_scope(repository, document_ids,
                           index_name=store.settings.es_index), limit)


def fuse(rankings, k=60):
    records, scores = {}, {}
    for hits in rankings:
        seen = set()
        for rank, hit in enumerate(hits, 1):
            key = (hit['document_id'], hit['version'], hit['chunk_id'])
            if key in seen:
                continue
            seen.add(key)
            records[key] = hit
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
    return [dict(records[key], score=scores[key])
            for key in sorted(records, key=lambda key: (-scores[key], key))]
