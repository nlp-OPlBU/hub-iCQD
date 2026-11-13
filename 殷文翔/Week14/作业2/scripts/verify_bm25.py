#!/usr/bin/env python3
"""Opt-in MinerU/MySQL/ES acceptance. Uses new document IDs; does not start services.

Run with py312. Retains uploaded PDF, normalized artifacts and DB task for inspection.
Kafka transport is tested separately; this invokes the worker directly.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

from app.core.config import Settings
from app.db.base import new_id
from app.db.session import create_db_engine, session_factory
from app.models import ParseTask
from app.repositories.document import DocumentRepository
from app.repositories.outbox import add_parse_requested
from app.schemas.document import ParseEvent
from app.services.pdf_processing import MineruTaskProcessor
from app.services.retrieval import retrieve
from app.services.storage import FileStorage
from app.workers.pdf_consumer import PdfWorker


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pdf', type=Path, required=True)
    parser.add_argument('--query', required=True)
    parser.add_argument('--tier', default='basic')
    parser.add_argument('--es-url', required=True)
    parser.add_argument('--es-index', required=True, help='Dedicated project acceptance index')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    settings = Settings(mineru_tier=args.tier, es_url=args.es_url, es_index=args.es_index,
                        es_analyzer='standard', retrieval_mode='bm25', task_max_retries=0)
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    doc, task = new_id(), new_id()
    event = ParseEvent(event_id=new_id(), document_id=doc, task_id=task, version=1,
                       storage_key=f'uploads/{doc}.pdf')
    start = time.monotonic()
    try:
        with args.pdf.open('rb') as source:
            saved = FileStorage(settings).save_pdf(source, args.pdf.name, 'application/pdf', doc, task)
        with factory() as session, session.begin():
            DocumentRepository(session).add_upload(document_id=doc, task_id=task,
                filename=saved.filename, storage_key=saved.storage_key, size_bytes=saved.size_bytes,
                sha256=saved.sha256, settings=settings)
            add_parse_requested(session, settings, event)
        FileStorage(settings).forget_receipt(doc)
        processor = MineruTaskProcessor(settings)
        outcome = PdfWorker(settings, factory, None, processor).handle_message(event.model_dump_json().encode())
        with factory() as session:
            stored = session.get(ParseTask, task)
            hits = retrieve(DocumentRepository(session), processor.index, args.query, document_ids=[doc])
            report = {'document_id': doc, 'task_id': task, 'outcome': outcome,
                      'seconds': round(time.monotonic() - start, 2), 'tier': args.tier,
                      'artifact_key': stored.artifact_key, 'error_code': stored.error_code,
                      'query': args.query, 'hits': hits, 'index_name': args.es_index,
                      'transport': 'direct worker invocation; Kafka not exercised'}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({'outcome': outcome, 'hits': len(hits), 'report': str(args.report)}, ensure_ascii=False))
        return 0 if outcome['outcome'] == 'completed' and hits else 1
    finally:
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
