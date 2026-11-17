"""At-least-once Outbox delivery. Kafka remains optional in local mode."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

from app.config import get_settings
from app.db import connect
from app.services import now


def iso_after(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")


class OutboxDispatcher:
    def __init__(self):
        self.settings = get_settings()
        self._producer = None

    async def start(self):
        if self.settings.adapter_mode == "real":
            try:
                from aiokafka import AIOKafkaProducer
            except ImportError as exc:
                raise RuntimeError("Install optional dependencies with pip install -e '.[services]'") from exc
            self._producer = AIOKafkaProducer(bootstrap_servers=self.settings.kafka_bootstrap_servers,
                                               acks="all", enable_idempotence=True)
            await self._producer.start()

    async def stop(self):
        if self._producer:
            await self._producer.stop()

    def claim(self):
        timestamp = now()
        with connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT o.id,o.event_uid,o.topic,o.payload,o.task_id FROM task_outbox o
                JOIN parse_tasks t ON t.id=o.task_id JOIN documents d ON d.id=t.document_id
                WHERE d.status <> 'deleting' AND t.status='pending'
                AND (o.status='pending' OR (o.status='publishing' AND o.locked_until < ?))
                AND (o.next_retry_at IS NULL OR o.next_retry_at <= ?)
                ORDER BY o.id LIMIT 1""", (timestamp, timestamp)).fetchone()
            if not row:
                return None
            changed = db.execute("UPDATE task_outbox SET status='publishing',locked_until=?,publish_count=publish_count+1,updated_at=? WHERE id=? AND (status='pending' OR locked_until < ?)",
                                 (iso_after(self.settings.outbox_lease_seconds), timestamp, row["id"], timestamp)).rowcount
            return dict(row) if changed == 1 else None

    def mark_sent(self, item):
        timestamp = now()
        with connect() as db:
            db.execute("UPDATE task_outbox SET status='sent',sent_at=?,locked_until=NULL,last_error=NULL,updated_at=? WHERE id=? AND status='publishing'",
                       (timestamp,timestamp,item["id"]))

    def mark_retry(self, item, error):
        timestamp = now()
        with connect() as db:
            db.execute("UPDATE task_outbox SET status='pending',locked_until=NULL,last_error=?,next_retry_at=?,updated_at=? WHERE id=? AND status='publishing'",
                       (str(error)[:1000],iso_after(2),timestamp,item["id"]))

    async def publish(self, item):
        if not self._producer:
            return
        await self._producer.send_and_wait(item["topic"], item["payload"].encode("utf-8"),
                                           key=json.loads(item["payload"])["task_uid"].encode())

    async def run(self):
        await self.start()
        try:
            while True:
                item = self.claim()
                if item is None:
                    await asyncio.sleep(self.settings.outbox_poll_seconds)
                    continue
                try:
                    # Local mode records a pending event in SQLite for a separate local worker.
                    await self.publish(item)
                    self.mark_sent(item)
                except Exception as exc:
                    self.mark_retry(item, exc)
                    await asyncio.sleep(1)
        finally:
            await self.stop()
