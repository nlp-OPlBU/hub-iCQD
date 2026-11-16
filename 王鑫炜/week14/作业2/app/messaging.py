import json
import logging
import time
import redis
from confluent_kafka import Producer
from confluent_kafka.admin import AdminClient, NewTopic
from .config import REDIS_URL, BROKERS, TOPIC
from .storage import db, log, now

logger = logging.getLogger(__name__)

def redis_client():
    return redis.Redis.from_url(REDIS_URL, decode_responses=True, socket_timeout=5,
                                socket_connect_timeout=3)

def producer():
    return Producer({'bootstrap.servers': BROKERS, 'enable.idempotence': True,
                     'acks': 'all', 'message.timeout.ms': 8000,
                     'socket.timeout.ms': 5000})

def ensure_topics():
    admin = AdminClient({'bootstrap.servers': BROKERS, 'socket.timeout.ms': 5000})
    existing = admin.list_topics(timeout=8).topics
    new = [NewTopic(t, num_partitions=1, replication_factor=1)
           for t in (TOPIC, TOPIC + '.dead') if t not in existing]
    if new:
        for f in admin.create_topics(new).values():
            f.result(timeout=12)

def send(p, topic, key, value):
    result = []
    def delivered(error, message):
        result.append(error)
    p.produce(topic, key=key, value=json.dumps(value, ensure_ascii=False).encode(),
              on_delivery=delivered)
    pending = p.flush(10)
    if pending or not result or result[0]:
        raise RuntimeError('Kafka 未确认消息，任务保留在 outbox 中等待重试')

def dispatch_once(p):
    with db() as c:
        pending = list(c.execute('SELECT * FROM outbox WHERE sent_at IS NULL ORDER BY rowid LIMIT 20'))
    for row in pending:
        try:
            send(p, TOPIC, row['doc_id'], json.loads(row['payload']))
        except Exception as e:
            with db() as c:
                c.execute('UPDATE outbox SET last_error=? WHERE event_id=?', (str(e), row['event_id']))
            raise
        with db() as c:
            c.execute('UPDATE outbox SET sent_at=?,last_error=NULL WHERE event_id=?',
                      (now(), row['event_id']))
            log(c, row['doc_id'], 'kafka_published', row['event_id'])
    return len(pending)

def dispatch_loop(stop):
    p = producer()
    while not stop.is_set():
        try:
            dispatch_once(p)
        except Exception as e:
            logger.warning('Dispatcher: %s', type(e).__name__)
        stop.wait(1)
    p.flush(3)
