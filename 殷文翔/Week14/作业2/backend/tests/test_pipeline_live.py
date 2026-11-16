"""Explicit real-server acceptance. Creates only UUID-named project resources.

Requires RAG_TEST_MYSQL_BINARY, RAG_PIPELINE_PDF, RAG_PIPELINE_KAFKA,
RAG_PIPELINE_ES, RAG_PIPELINE_LLM; shared services must already be running.
Launches project API/dispatcher/Worker and an independent loopback Nginx.
"""
import base64
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer, TopicPartition
from kafka.admin import NewTopic
from PIL import Image
import pytest
from sqlalchemy import select, text

from app.core.config import PROJECT_ROOT, Settings
from app.db.session import create_db_engine, session_factory
from app.models import Document, OutboxEvent, ParseTask
from app.services.elasticsearch_store import ElasticsearchStore


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def eventually(check, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = check()
        if last:
            return last
        time.sleep(.25)
    raise AssertionError(f'Timed out after {timeout}s; last result: {last!r}')


@contextmanager
def observed_model(upstream, observations):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers['Content-Length']))
            payload = json.loads(raw)
            images = []
            for message in payload['messages']:
                if not isinstance(message['content'], list):
                    continue
                for part in message['content']:
                    if part['type'] == 'image_url':
                        url = part['image_url']['url']
                        assert url.startswith('data:image/')
                        data = base64.b64decode(url.split(',', 1)[1], validate=True)
                        with Image.open(io.BytesIO(data)) as img:
                            img.load()
                            images.append({'bytes': len(data), 'size': img.size,
                                           'sha256': hashlib.sha256(data).hexdigest()})
            with httpx.Client(timeout=150, trust_env=False) as client:
                response = client.post(upstream.rstrip('/') + '/chat/completions', content=raw,
                    headers={'Content-Type': 'application/json',
                             'Authorization': self.headers.get('Authorization', '')})
            observations.append({'images': images, 'status': response.status_code})
            self.send_response(response.status_code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(response.content)))
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not all(os.environ.get(key) for key in (
    'RAG_PIPELINE_PDF', 'RAG_PIPELINE_KAFKA', 'RAG_PIPELINE_ES', 'RAG_PIPELINE_LLM')),
    reason='Explicit PDF/Kafka/ES/vision endpoint opt-in required')
def test_real_upload_queue_crash_faults_versions_and_vision(isolated_mysql):
    root_engine, mysql = isolated_mysql
    run_id = uuid4().hex
    work = PROJECT_ROOT / 'runtime/pipeline-acceptance' / run_id
    work.mkdir(parents=True)
    report = {'run_id': run_id, 'status': 'failed', 'steps': {}, 'model_requests': []}
    database = 'rag_pipeline_' + run_id
    prefix = 'multimodal-rag-pipeline-' + run_id
    topic, failed_topic, group = prefix + '.parse', prefix + '.failed', prefix + '.workers'
    api_port, nginx_port = free_port(), free_port()
    children, logs = [], []
    engine = admin = producer = failure_consumer = None
    created_topics, created_indices = [], []
    pdf = Path(os.environ['RAG_PIPELINE_PDF']).resolve(strict=True)
    settings = Settings(_env_file=None, mysql_host=mysql.mysql_host, mysql_port=mysql.mysql_port,
        mysql_user=mysql.mysql_user, mysql_password=mysql.mysql_password, mysql_database=database,
        data_dir=work / 'data', log_dir=work / 'logs', app_host='127.0.0.1', app_port=api_port,
        kafka_bootstrap_servers=os.environ['RAG_PIPELINE_KAFKA'], kafka_pdf_topic=topic,
        kafka_failed_topic=failed_topic, kafka_consumer_group=group,
        task_lease_seconds=12, task_heartbeat_seconds=2, task_retry_base_seconds=1,
        task_max_retries=1, outbox_poll_seconds=1, outbox_lease_seconds=5,
        es_url=os.environ['RAG_PIPELINE_ES'], es_index=prefix, mineru_tier='basic',
        mineru_command=str(Path(sys.executable).parent / 'mineru-kit'),
        llm_base_url=os.environ['RAG_PIPELINE_LLM'], llm_vision_enabled=True,
        image_max_context_count=4, retrieval_top_k=2)
    environment = os.environ.copy()
    for field in ('mysql_host', 'mysql_port', 'mysql_user', 'mysql_database', 'app_host', 'app_port',
        'data_dir', 'log_dir', 'kafka_bootstrap_servers', 'kafka_pdf_topic', 'kafka_failed_topic',
        'kafka_consumer_group', 'task_lease_seconds', 'task_heartbeat_seconds', 'task_retry_base_seconds',
        'task_max_retries', 'outbox_poll_seconds', 'outbox_lease_seconds', 'es_url', 'es_index',
        'mineru_tier', 'mineru_command', 'mineru_home', 'mineru_config', 'llm_base_url',
        'llm_vision_enabled', 'image_max_context_count', 'retrieval_top_k'):
        value = getattr(settings, field)
        environment[field.upper()] = str(value).lower() if isinstance(value, bool) else str(value)
    environment.update(MYSQL_PASSWORD=settings.mysql_password.get_secret_value(),
        LLM_API_KEY='EMPTY', LLM_MODEL='rag-vlm', APP_DEBUG='false', RETRIEVAL_MODE='bm25',
        RERANK_ENABLED='false', ES_USERNAME='', ES_PASSWORD='', ES_CA_CERTS='', ES_ANALYZER='standard',
        MINERU_DEVICE_MODE='cpu', HF_HUB_OFFLINE='1', OMP_NUM_THREADS='2', MKL_NUM_THREADS='2',
        PATH=str(Path(sys.executable).parent) + os.pathsep + environment.get('PATH', ''))

    def launch(name, script, overrides=None):
        log = (work / (name + '.log')).open('wb')
        logs.append(log)
        child = subprocess.Popen(['bash', str(PROJECT_ROOT / 'scripts' / script)],
            cwd=PROJECT_ROOT, env={**environment, **(overrides or {})}, stdout=log, stderr=log,
            start_new_session=True)
        children.append(child)
        return child

    def stop(child, hard=False):
        if child.poll() is None:
            child.send_signal(signal.SIGKILL if hard else signal.SIGTERM)
            try:
                child.wait(timeout=45)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)

    def task_row(task_id):
        with session_factory(engine)() as session:
            task = session.get(ParseTask, task_id)
            return {'status': task.status, 'attempts': task.attempts, 'error': task.error_code,
                'details': task.error_details, 'artifact_key': task.artifact_key,
                'version': task.version, 'finished': bool(task.finished_at)}

    def event_row(task_id, event_type='pdf.parse.requested'):
        with session_factory(engine)() as session:
            row = session.scalar(select(OutboxEvent).where(OutboxEvent.task_id == task_id,
                                                         OutboxEvent.event_type == event_type))
            return {'status': row.status, 'attempts': row.attempts, 'error': row.error_code,
                    'payload': row.payload} if row else None

    try:
        with root_engine.begin() as connection:
            connection.execute(text(f'CREATE DATABASE `{database}` CHARACTER SET utf8mb4'))
        engine = create_db_engine(settings)
        config = Config(str(PROJECT_ROOT / 'backend/alembic.ini'))
        with engine.connect() as connection:
            config.attributes['connection'] = connection
            command.upgrade(config, 'head')
            connection.commit()
        admin = KafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers,
                                 request_timeout_ms=10000)
        admin.create_topics([NewTopic(topic, 1, 1), NewTopic(failed_topic, 1, 1)])
        created_topics.extend([topic, failed_topic])
        created_indices.extend([settings.es_index, settings.es_index + '-bad'])
        producer = KafkaProducer(bootstrap_servers=settings.kafka_bootstrap_servers, acks='all',
                                value_serializer=lambda value: json.dumps(value).encode())
        report['resources'] = {'database': database, 'topic': topic, 'failed_topic': failed_topic,
                               'index': settings.es_index, 'api_port': api_port, 'nginx_port': nginx_port}
        with observed_model(settings.llm_base_url, report['model_requests']) as proxy:
            environment['LLM_BASE_URL'] = proxy
            api = launch('api', 'start_api.sh')
            with httpx.Client(base_url=f'http://127.0.0.1:{api_port}', timeout=150, trust_env=False) as direct:
                def ready():
                    assert api.poll() is None, 'API stopped; inspect api.log'
                    try:
                        return direct.get('/api/health/ready').status_code == 200
                    except httpx.ConnectError:
                        return False
                eventually(ready, 20)
            sys.path.insert(0, str(PROJECT_ROOT / 'scripts'))
            from render_nginx import render
            nginx_config = render(work / 'nginx', port=nginx_port,
                                  api_origin=f'http://127.0.0.1:{api_port}')
            nginx_cmd = ['nginx', '-p', str(nginx_config.parent) + '/', '-c', str(nginx_config)]
            subprocess.run([*nginx_cmd, '-t'], check=True, capture_output=True)
            log = (work / 'nginx.log').open('wb')
            logs.append(log)
            nginx = subprocess.Popen([*nginx_cmd, '-g', 'daemon off;'], stdout=log, stderr=log)
            children.append(nginx)
            with httpx.Client(base_url=f'http://127.0.0.1:{nginx_port}', timeout=150, trust_env=False) as client:
                def nginx_ready():
                    assert nginx.poll() is None
                    try:
                        return client.get('/api/health/ready').status_code == 200
                    except httpx.ConnectError:
                        return False
                eventually(nginx_ready, 10)
                assert 'text/html' in client.get('/documents').headers['content-type']
                assert client.get('/api/nonexistent').status_code == 404
                report['steps']['nginx'] = 'config/static SPA/API routes passed'

                def upload(count=1):
                    response = client.post('/api/documents/batch', files=[
                        ('files', ('test2.pdf', pdf.read_bytes(), 'application/pdf')) for _ in range(count)])
                    assert response.status_code == 200, response.text
                    batch = response.json()
                    assert batch['accepted'] == count and batch['rejected'] == 0, batch
                    return [row['document'] for row in batch['results']]

                def wait_task(task_id, status, timeout=180):
                    def check():
                        response = client.get('/api/tasks/' + task_id)
                        assert response.status_code == 200, response.text
                        row = response.json()
                        assert row['status'] != 'failed' or status == 'failed', row
                        return row if row['status'] == status else None
                    return eventually(check, timeout)

                documents = upload(2)
                ids = [doc['id'] for doc in documents]
                tids = [doc['latest_task']['id'] for doc in documents]
                assert len(set(ids)) == 2 and all(doc['latest_task']['status'] == 'pending' for doc in documents)
                assert client.get('/api/documents', params={'status': 'pending'}).json()['total'] == 2
                # A real network failure, no publisher mock: only this process points to a closed port.
                dispatcher = launch('dispatcher-offline', 'start_dispatcher.sh',
                    {'KAFKA_BOOTSTRAP_SERVERS': f'127.0.0.1:{free_port()}',
                     'KAFKA_PUBLISH_TIMEOUT_SECONDS': '2'})
                eventually(lambda: (event_row(tids[0])['error'] == 'KAFKA_UNAVAILABLE'), 20)
                stop(dispatcher)
                assert all(task_row(tid)['status'] == 'pending' for tid in tids)
                report['steps']['outbox_compensation'] = [event_row(tid) for tid in tids]
                dispatcher = launch('dispatcher', 'start_dispatcher.sh')
                eventually(lambda: all(event_row(tid)['status'] == 'published' for tid in tids), 20)
                # Kill a real Worker after its lease is durably visible via HTTP, then recover it.
                worker = launch('worker-crash', 'start_worker.sh')
                processing = wait_task(tids[0], 'processing', 30)
                stop(worker, hard=True)
                eventually(lambda: task_row(tids[0])['status'] == 'pending', 25)
                assert task_row(tids[0])['error'] == 'WORKER_LOST'
                report['steps']['worker_crash'] = {'before': processing, 'recovered': task_row(tids[0])}
                worker = launch('worker-success', 'start_worker.sh')
                for tid in tids:
                    wait_task(tid, 'completed', 180)
                assert task_row(tids[0])['attempts'] == 2 and task_row(tids[1])['attempts'] == 1
                report['steps']['batch'] = [task_row(tid) for tid in tids]
                # Repeat acknowledged messages through Kafka and await the actual committed offset.
                snapshots = [task_row(tid) for tid in tids]
                for tid in tids:
                    metadata = producer.send(topic, event_row(tid)['payload']).get(timeout=10)
                tp = TopicPartition(topic, metadata.partition)
                def duplicate_committed():
                    offset = admin.list_group_offsets({group: [tp]})[group].get(tp)
                    return offset and offset.offset > metadata.offset
                eventually(duplicate_committed, 20)
                assert [task_row(tid) for tid in tids] == snapshots
                report['steps']['duplicate'] = 'Kafka redelivery committed; attempts/artifact references unchanged'
                response = client.post('/api/search', json={'text': '购物车 100', 'document_ids': [ids[0]]})
                assert response.status_code == 200 and response.json(), response.text
                assert all(hit['source']['document_id'] == ids[0] for hit in response.json())
                assert any('100' in hit['text'] and hit['source']['page'] == 2 for hit in response.json())
                report['steps']['search'] = response.json()
                stop(worker)
                # Disable automatic retries only for the following fault-injection documents.
                stop(api)
                environment['TASK_MAX_RETRIES'] = '0'
                api = launch('api-faults', 'start_api.sh')
                eventually(nginx_ready, 10)
                timeout_doc = upload()[0]
                timeout_tid = timeout_doc['latest_task']['id']
                worker = launch('worker-timeout', 'start_worker.sh', {'MINERU_TIMEOUT_SECONDS': '1'})
                wait_task(timeout_tid, 'failed', 30)
                assert task_row(timeout_tid)['error'] == 'MINERU_TIMEOUT'
                assert task_row(timeout_tid)['finished']
                stop(worker)
                report['steps']['timeout'] = task_row(timeout_tid)
                # Real ES bulk item rejection after real PDF parsing, isolated to an owned bad index.
                bad_settings = settings.model_copy(update={'es_index': settings.es_index + '-bad'})
                bad = ElasticsearchStore(bad_settings)
                bad._json('PUT', '/' + bad_settings.es_index,
                          {'mappings': {'properties': {'text': {'type': 'integer'}}}})
                index_doc = upload()[0]
                index_tid = index_doc['latest_task']['id']
                worker = launch('worker-index-failure', 'start_worker.sh', {'ES_INDEX': bad_settings.es_index})
                wait_task(index_tid, 'failed', 180)
                assert task_row(index_tid)['error'] == 'INDEX_UNAVAILABLE'
                with session_factory(engine)() as session:
                    assert session.get(Document, index_doc['id']).current_searchable_version is None
                stop(worker)
                report['steps']['index_failure'] = task_row(index_tid)
                for tid in (timeout_tid, index_tid):
                    eventually(lambda: event_row(tid, 'pdf.parse.failed')
                        and event_row(tid, 'pdf.parse.failed')['status'] == 'published', 20)
                failure_consumer = KafkaConsumer(failed_topic,
                    bootstrap_servers=settings.kafka_bootstrap_servers, auto_offset_reset='earliest',
                    enable_auto_commit=False, group_id=None)
                failures = set()
                def read_failures():
                    for records in failure_consumer.poll(timeout_ms=500).values():
                        failures.update(json.loads(record.value)['task_id'] for record in records)
                    return {timeout_tid, index_tid} <= failures
                eventually(read_failures, 15)
                report['steps']['failure_topic'] = sorted(failures)
                # Deliberately retain a v1 chunk. API must filter it out once v2 becomes current.
                store = ElasticsearchStore(settings)
                old_id = index_doc['id'] + ':1:1:0:0'
                store.index_chunks([{'chunk_id': old_id, 'document_id': index_doc['id'], 'version': 1,
                    'filename': 'test2.pdf', 'page': 1, 'text': 'retiredversionsentinel',
                    'image_keys': [], 'page_image_keys': []}])
                assert client.post('/api/search', json={'text': 'retiredversionsentinel',
                    'document_ids': [index_doc['id']]}).status_code == 422
                retry = client.post('/api/documents/' + index_doc['id'] + '/retry',
                    json={'expected_latest_version': 1}, headers={'Idempotency-Key': str(uuid4())})
                assert retry.status_code == 202, retry.text
                retry_tid = retry.json()['id']
                # Old failed-task record arrives after v2 exists; it must be harmless.
                producer.send(topic, event_row(index_tid)['payload']).get(timeout=10)
                worker = launch('worker-version2', 'start_worker.sh')
                wait_task(retry_tid, 'completed', 180)
                assert client.post('/api/search', json={'text': 'retiredversionsentinel',
                    'document_ids': [index_doc['id']]}).json() == []
                current = client.post('/api/search', json={'text': '购物车 100',
                    'document_ids': [index_doc['id']]}).json()
                assert current and all(hit['source']['version'] == 2 for hit in current)
                assert store._json('GET', f'/{settings.es_index}/_doc/{old_id}')['found']
                assert task_row(index_tid)['status'] == 'failed'
                report['steps']['old_version_isolation'] = {'retained_old_chunk': old_id, 'current': current}
                stop(worker)
                for status in ('pending', 'processing', 'completed', 'failed'):
                    assert client.get('/api/documents', params={'status': status}).status_code == 200
                assert client.get('/api/documents', params={'status': 'failed'}).json()['total'] == 1
                # Real application images, real proxy-forwarded model calls, source content via Nginx.
                conversation = client.post('/api/conversations', json={'title': '完整链路验收'}).json()['id']
                path = '/api/conversations/' + conversation
                answer = client.post(path + '/messages', json={'text': '购物车最多可以添加多少件商品？',
                    'document_ids': [ids[0]]}, headers={'Idempotency-Key': str(uuid4())})
                assert answer.status_code == 200, answer.text
                text_answer = answer.json()['assistant_message']
                assert text_answer['status'] == 'completed' and '100' in text_answer['content'], text_answer
                artifact = settings.data_dir / task_row(tids[0])['artifact_key']
                original_image = artifact / 'images/page_0_image_5.jpg'
                uploaded = client.post(path + '/images', files=[
                    ('files', ('original.jpg', original_image.read_bytes(), 'image/jpeg'))])
                assert uploaded.status_code == 201, uploaded.text
                image = uploaded.json()['items'][0]
                assert client.get(image['url']).content == original_image.read_bytes()
                question = {'text': '图中底部流程中间的方框内写着什么？购物车最多可以添加多少件商品？',
                            'image_ids': [image['id']], 'document_ids': [ids[0]]}
                key = str(uuid4())
                answer = client.post(path + '/messages', json=question, headers={'Idempotency-Key': key})
                assert answer.status_code == 200, answer.text
                image_answer = answer.json()['assistant_message']
                assert image_answer['status'] == 'completed', image_answer
                assert '问答技能' in image_answer['content'] and '100' in image_answer['content'], image_answer
                assert image_answer['sources']
                for source in image_answer['sources']:
                    assert source['document_id'] == ids[0] and 1 <= source['page'] <= 6
                    for resource in source['images']:
                        assert client.get(resource['url']).status_code == 200
                request_count = len(report['model_requests'])
                assert client.post(path + '/messages', json=question,
                    headers={'Idempotency-Key': key}).json() == answer.json()
                assert len(report['model_requests']) == request_count
                assert len(report['model_requests'][-2]['images']) == 1
                assert len(report['model_requests'][-1]['images']) >= 2
                assert all(item['status'] == 200 for item in report['model_requests'])
                report['steps']['vision'] = {'user_image': image, 'text_answer': text_answer,
                                            'image_answer': image_answer}
        report['status'] = 'passed'
    finally:
        for child in reversed(children):
            stop(child)
        for log in logs:
            log.close()
        if failure_consumer:
            failure_consumer.close()
        if producer:
            producer.close(timeout=5)
        cleanup = []
        if admin:
            try:
                admin.delete_groups([group])
                if created_topics:
                    admin.delete_topics(created_topics)
                cleanup.append('owned Kafka topics/group removed')
            except Exception as exc:
                cleanup.append('Kafka cleanup pending: ' + type(exc).__name__)
            admin.close()
        if engine:
            engine.dispose()
        with root_engine.begin() as connection:
            connection.execute(text(f'DROP DATABASE IF EXISTS `{database}`'))
        cleanup.append('owned database removed')
        for index in created_indices:
            try:
                status, _ = ElasticsearchStore(settings)._request('DELETE', '/' + index)
                assert status in (200, 404)
            except Exception as exc:
                cleanup.append('index cleanup pending: ' + index + ' ' + type(exc).__name__)
        report['cleanup'] = cleanup
        (work / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('Acceptance report:', work / 'report.json')
