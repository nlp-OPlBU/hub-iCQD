import json
import logging
import signal
import time
from confluent_kafka import Consumer, KafkaException
from .config import BROKERS, TOPIC, GROUP, PARSE_TIMEOUT
from .messaging import redis_client, producer, send, ensure_topics
from .parser import parse_pdf
from .storage import init, db, log, now

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

def process_job(job, parser=parse_pdf):
    doc_id, attempt = job['document_id'], int(job['attempt'])
    client = redis_client()
    lock = client.lock(f'wxw14:parse:{doc_id}', timeout=PARSE_TIMEOUT + 120, blocking_timeout=1)
    if not lock.acquire():
        return 'busy'
    try:
        with db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=?', (doc_id,)).fetchone()
            if not row or row['attempt'] != attempt or row['status'] == 'completed':
                return 'duplicate'
            if row['status'] == 'failed':
                # A previous crash may have happened before DLQ acknowledgement or offset commit.
                return 'failed'
            c.execute("UPDATE documents SET status='processing',error=NULL,updated_at=? WHERE id=?", (now(), doc_id))
            log(c, doc_id, 'processing', f'worker started attempt={attempt}')
        logger.info('PROCESSING document=%s attempt=%s', doc_id, attempt)
        try:
            chunks, parser_name = parser(doc_id)
            with db() as c:
                c.execute('DELETE FROM chunks WHERE doc_id=?', (doc_id,))
                c.executemany('INSERT INTO chunks(id,doc_id,page,kind,text,image_url) VALUES(:id,:doc_id,:page,:kind,:text,:image_url)', chunks)
                image_count = sum(bool(x['image_url']) for x in chunks)
                c.execute("UPDATE documents SET status='completed',parser=?,chunk_count=?,image_count=?,error=NULL,updated_at=? WHERE id=?",
                          (parser_name, len(chunks), image_count, now(), doc_id))
                log(c, doc_id, 'completed', f'{parser_name}; chunks={len(chunks)}; images={image_count}')
            logger.info('COMPLETED document=%s chunks=%d images=%d', doc_id, len(chunks), image_count)
            return 'completed'
        except Exception as e:
            # Do not leak upstream HTTP bodies, URLs, local paths or secrets to the client.
            message = str(e) if isinstance(e, (ValueError, RuntimeError)) else f'MinerU 解析异常（{type(e).__name__}）'
            with db() as c:
                c.execute("UPDATE documents SET status='failed',error=?,updated_at=? WHERE id=?", (message, now(), doc_id))
                log(c, doc_id, 'failed', message)
            logger.exception('FAILED document=%s', doc_id)
            return 'failed'
    finally:
        try:
            lock.release()
        except Exception:
            logger.warning('Parse lock expired')

def run():
    init()
    ensure_topics()
    consumer = Consumer({'bootstrap.servers': BROKERS, 'group.id': GROUP,
                         'auto.offset.reset': 'earliest', 'enable.auto.commit': False,
                         'max.poll.interval.ms': (PARSE_TIMEOUT + 180) * 1000,
                         'session.timeout.ms': 45000})
    consumer.subscribe([TOPIC])
    p = producer()
    running = True
    def stop(*args):
        nonlocal running
        running = False
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info('Worker ready topic=%s group=%s', TOPIC, GROUP)
    try:
        while running:
            redis_client().set('wxw14:worker:heartbeat', now(), ex=PARSE_TIMEOUT + 120)
            message = consumer.poll(1)
            if message is None:
                continue
            if message.error():
                raise KafkaException(message.error())
            job = json.loads(message.value())
            state = process_job(job)
            if state == 'busy':
                # Rewind this partition; a held lock never acknowledges unfinished work.
                from confluent_kafka import TopicPartition
                consumer.seek(TopicPartition(message.topic(), message.partition(), message.offset()))
                time.sleep(1)
                continue
            if state == 'failed':
                send(p, TOPIC + '.dead', job['document_id'], {**job, 'error': 'parse_failed'})
            consumer.commit(message=message, asynchronous=False)
            with db() as c:
                log(c, job['document_id'], 'offset_committed', f'{message.topic()}:{message.partition()}:{message.offset()}')
            logger.info('COMMITTED offset=%d state=%s', message.offset(), state)
    finally:
        consumer.close()

if __name__ == '__main__':
    run()
