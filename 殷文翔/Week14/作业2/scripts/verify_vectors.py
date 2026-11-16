"""Read-only real model/index checks using an already completed QA sample document."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings
from app.db.session import create_db_engine, session_factory
from app.repositories.document import DocumentRepository
from app.services.evidence import read_evidence
from app.services.chunking import build_chunks
from app.services.embedding import EmbeddingClient
from app.services.vector_index import VectorIndex
from app.services.elasticsearch_store import ElasticsearchStore
from app.services.retrieval import search_snapshot


def main():
    settings = get_settings().model_copy(update={'retrieval_mode': 'hybrid'})
    engine = create_db_engine(settings)
    with session_factory(engine)() as session:
        repository = DocumentRepository(session)
        versions = repository.searchable_versions(index_name=settings.es_index)
        for document, version in versions.items():
            doc, task = repository.source_record(document, version)
            if doc.original_filename == 'test2.pdf':
                chunks = build_chunks(document_id=document, version=version, filename=doc.original_filename,
                    artifact=read_evidence(settings, task))
                break
        else:
            raise RuntimeError('Upload QA sample test2.pdf before this acceptance check')
    engine.dispose()
    scope = {document: version}
    encoder, store = EmbeddingClient(settings), VectorIndex(settings)
    queries = ['TableQA', '如何从表格数据里找到问题的答案？', '怎样用知识图谱回答自然语言问题？']
    report = {'dimensions': settings.embedding_dimensions, 'queries': []}
    for query in queries:
        vector = encoder.encode(query)
        semantic = store.search_vector(vector, scope, 5)
        fused = search_snapshot(ElasticsearchStore(settings), query, scope, 5)
        assert semantic and fused
        assert all(h['document_id'] == document and h['version'] == version for h in semantic + fused)
        report['queries'].append({'query': query, 'vector_pages': [h['page'] for h in semantic],
                                 'hybrid_pages': [h['page'] for h in fused],
                                 'top_evidence': fused[0]['text'][:140]})
    illustrated = next(c for c in chunks if c['image_keys'])
    part = encoder.image_part(illustrated['image_keys'][0])
    image_vector = encoder.encode('', [part])
    image_hits = store.search_vector(image_vector, scope, 10)
    assert any(h['page'] == illustrated['page'] for h in image_hits)
    combined = search_snapshot(ElasticsearchStore(settings), '这张图说明了哪种问答方法？', scope, 10, images=[part])
    assert any(h['page'] == illustrated['page'] for h in combined)
    assert not store.search_vector(image_vector, {document: version + 1000}, 5)
    assert not store.search_vector(image_vector, {'nonexistent-document': 1}, 5)
    report.update(image_page=illustrated['page'], image_hit_pages=[h['page'] for h in image_hits],
                  image_hybrid_pages=[h['page'] for h in combined], scope_and_version_filter=True)
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
