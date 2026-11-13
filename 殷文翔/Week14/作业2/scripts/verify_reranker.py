"""Real acceptance against the configured services and an uploaded test2.pdf sample.

Creates three acceptance conversations and uploads a sample document image. Does
not change shared services or indexes. Reports omit original image bytes.
"""
import argparse
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from fastapi.testclient import TestClient
from app.core.config import get_settings
from app.db.session import create_db_engine, session_factory
from app.main import create_app
from app.repositories.document import DocumentRepository
from app.services.chunking import build_chunks
from app.services.evidence import read_evidence
from app.services.embedding import EmbeddingClient
from app.services.images import resource_path
from app.services.rag import retrieve_evidence, sources_for_hits
from app.services.reranker import RerankerClient
from app.services.llm_client import LLMClient


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    settings = get_settings().model_copy(update={'rerank_enabled': True, 'retrieval_mode': 'hybrid'})
    engine = create_db_engine(settings)
    factory = session_factory(engine)
    with factory() as session:
        repo = DocumentRepository(session)
        version = repo.searchable_versions([args.document_id], index_name=settings.es_index)[args.document_id]
        doc, task = repo.source_record(args.document_id, version)
        chunks = build_chunks(document_id=doc.id, version=version, filename=doc.original_filename,
                              artifact=read_evidence(settings, task))
    scope = {args.document_id: version}
    # Select actual table pixels with a known factual value, not a generated image.
    illustrated = next(c for c in chunks if c['page'] == 4 and c['evidence_type'] == 'table')
    part = EmbeddingClient(settings).image_part(illustrated['image_keys'][0])
    report = {'document_id': args.document_id, 'version': version, 'ranking': []}
    direct = RerankerClient(settings).score('如何从表格数据里找到问题的答案？', [
        {'content': [{'type': 'text', 'text': '今天的天气晴朗。'}]},
        {'content': [{'type': 'text', 'text': 'TableQA 从表格中检索并回答自然语言问题。'}]},
        {'content': [part]}])
    assert direct[1] > direct[0]
    report['text_and_image_scores'] = direct
    for question, expected in [('如何从表格数据里找到问题的答案？', 3),
                               ('图中 mate40 的价格是多少？', 4)]:
        images = [part] if expected == 4 else None
        started = time.monotonic()
        hits, warning = retrieve_evidence(factory, settings, question, scope, 6, images=images)
        assert warning is None and hits[0]['page'] == expected
        assert all(h['rerank_status'] == 'completed' for h in hits)
        assert [h['score'] for h in hits] == sorted([h['score'] for h in hits], reverse=True)
        sources_for_hits(factory, settings, hits)  # canonical block/page/image check
        report['ranking'].append({'question': question, 'seconds': round(time.monotonic()-started, 2),
            'hits': [{k: h.get(k) for k in ('chunk_id', 'page', 'evidence_type', 'retrieval_score', 'rerank_score')}
                     for h in hits]})
    # Record shape of real requests, then delegate unchanged to the actual visual model.
    original = LLMClient.complete
    calls = []
    def observed(self, messages, **kwargs):
        parts = messages[-1]['content']
        calls.append({'labels': [p['text'] for p in parts if p['type'] == 'text' and
                                (p['text'] == '用户原图' or p['text'].startswith('文档图片'))],
                      'image_count': sum(p['type'] == 'image_url' for p in parts),
                      'all_images_encoded': all(p['image_url']['url'].startswith('data:image/')
                                               for p in parts if p['type'] == 'image_url')})
        return original(self, messages, **kwargs)
    LLMClient.complete = observed
    try:
        with TestClient(create_app(settings)) as client:
            cid = client.post('/api/conversations', json={'title': '图文重排真实验收'}).json()['id']
            data = resource_path(settings, illustrated['image_keys'][0]).read_bytes()
            upload = client.post(f'/api/conversations/{cid}/images',
                files={'files': ('table.jpg', data, 'image/jpeg')})
            upload.raise_for_status()
            image_id = upload.json()['items'][0]['id']
            response = client.post(f'/api/conversations/{cid}/messages', headers={'Idempotency-Key': str(uuid4())},
                json={'text': '请根据图中表格和文档说明，mate40 的价格是多少？引用文档来源。',
                      'image_ids': [image_id], 'document_ids': [args.document_id]})
            response.raise_for_status()
            answer = response.json()['assistant_message']
            assert answer['status'] == 'completed' and answer['sources'], answer
            assert any(s['page'] == 4 for s in answer['sources'])
            assert calls[-1]['image_count'] >= 2 and '用户原图' in calls[-1]['labels']
            assert any(label.startswith('文档图片') for label in calls[-1]['labels'])
            assert all(c['all_images_encoded'] for c in calls)
            for source in answer['sources']:
                assert source['document_id'] == args.document_id and source['rerank_score'] is not None
                for image in source['images']:
                    assert client.get(image['url']).status_code == 200
            report['answer'] = answer
            report['model_input_shapes'] = list(calls)
            missing = client.post(f'/api/conversations/{cid}/messages', headers={'Idempotency-Key': str(uuid4())},
                json={'text': '文档里提到火星银行2026年营业利润是多少？如果没有依据请明确说明。',
                      'document_ids': [args.document_id]}).json()['assistant_message']
            assert missing['status'] == 'completed' and '依据不足' in missing['content'] and not missing['sources'], missing
            report['no_evidence'] = missing
        # Closed local endpoint simulates an outage, without stopping the live scorer.
        degraded = settings.model_copy(update={'rerank_base_url': 'http://127.0.0.1:1', 'rerank_timeout_seconds': 2})
        with TestClient(create_app(degraded)) as client:
            body = {'text': '表格问答是什么？', 'document_ids': [args.document_id]}
            search = client.post('/api/search', json=body).json()
            assert search and all(h['rerank_status'] == 'degraded' for h in search)
            cid = client.post('/api/conversations', json={'title': '重排降级验收'}).json()['id']
            answer = client.post(f'/api/conversations/{cid}/messages', headers={'Idempotency-Key': str(uuid4())},
                                 json=body).json()['assistant_message']
            assert answer['status'] == 'completed' and '降级' in answer['content'], answer
            report['degraded_answer'] = answer
    finally:
        LLMClient.complete = original
        engine.dispose()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'report': str(args.output), 'ranking': report['ranking']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
