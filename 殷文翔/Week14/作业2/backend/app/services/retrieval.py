"""BM25 retrieval limited to MySQL's completed, current document versions."""
from app.repositories.document import DocumentRepository
from app.services.elasticsearch_store import ElasticsearchStore


def retrieve(repository: DocumentRepository, store: ElasticsearchStore, query: str,
             *, document_ids: list[str] | None = None, limit: int = 10) -> list[dict]:
    return store.search(query, versions=repository.searchable_versions(document_ids), limit=limit)
