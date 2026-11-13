"""Prepare, resume/catch up, verify, activate or roll back a vector generation.

Repeated backfill scans only current completed artifacts; successful chunk writes
are durable checkpoints. No changes to task versions, original index or files.
"""
import argparse
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings
from app.db.session import create_db_engine, session_factory
from app.repositories.document import DocumentRepository
from app.services.chunking import build_chunks
from app.services.evidence import read_evidence
from app.services.vector_index import VectorIndex


def run(settings, action, index):
    store = VectorIndex(settings)
    if action == 'prepare':
        store.prepare(index)
        return {'prepared': index}
    store.check(index)
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    failures, count = [], 0
    try:
        with factory() as session:
            repository = DocumentRepository(session)
            if action == 'activate' and repository.has_processing_tasks():
                return {'ok': False, 'failures': [{'error': 'PROCESSING_TASKS',
                    'message': 'Wait for processing tasks to finish before activating a generation'}]}
            versions = repository.searchable_versions(index_name=settings.es_index)
        for document_id, version in versions.items():
            try:
                with factory() as session:
                    doc, task = DocumentRepository(session).source_record(document_id, version)
                    artifact = read_evidence(settings, task)
                    chunks = build_chunks(document_id=document_id, version=version,
                        filename=doc.original_filename, artifact=artifact)
                if not chunks:
                    raise ValueError('No evidence chunks')
                if action == 'backfill':
                    store.index_chunks(chunks, target=index)
                if store.missing(index, chunks):
                    raise ValueError('Generation incomplete')
                count += len(chunks)
            except Exception as exc:
                failures.append({'document_id': document_id, 'version': version, 'error': type(exc).__name__})
        if failures:
            return {'ok': False, 'failures': failures, 'chunks': count}
        result = {'ok': True, 'documents': len(versions), 'chunks': count}
        if action == 'activate':
            result['previous'] = store.activate(index)
        return result
    finally:
        engine.dispose()


if __name__ == '__main__':
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'backfill', 'verify', 'activate'])
    parser.add_argument('index')
    args = parser.parse_args()
    result = run(get_settings(), args.action, args.index)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(1 if result.get('ok') is False else 0)
