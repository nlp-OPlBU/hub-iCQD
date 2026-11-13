"""BM25 retrieval limited to MySQL's completed, current document versions."""
from app.repositories.document import DocumentRepository
from app.services.elasticsearch_store import ElasticsearchStore
from app.api.errors import ApiError


def document_scope(repository: DocumentRepository, document_ids: list[str] | None, *, index_name=None) -> dict[str, int]:
    versions = repository.searchable_versions(document_ids, index_name=index_name)
    if document_ids is not None and (not document_ids or set(versions) != set(document_ids)):
        raise ApiError(422, 'INVALID_DOCUMENT_SCOPE', '指定范围包含不存在或不可检索的文档')
    return versions


def search_snapshot(store: ElasticsearchStore, query: str, versions: dict[str, int], limit: int):
    hits = store.search(query, versions=versions, limit=limit)
    # Defense in depth: never expose an out-of-scope/stale hit, even with a bad ES response.
    for hit in hits:
        if (versions.get(hit.get('document_id')) != hit.get('version') or not hit.get('chunk_id')
                or not isinstance(hit.get('text'), str) or not hit['text'].strip()
                or (hit.get('page') is not None and
                    (type(hit['page']) is not int or hit['page'] < 1))):
            raise ApiError(503, 'INVALID_SEARCH_RESULT', '检索来源校验失败')
    return hits


def retrieve(repository: DocumentRepository, store: ElasticsearchStore, query: str,
             *, document_ids: list[str] | None = None, limit: int = 10) -> list[dict]:
    return search_snapshot(store, query, document_scope(repository, document_ids,
                           index_name=store.settings.es_index), limit)
