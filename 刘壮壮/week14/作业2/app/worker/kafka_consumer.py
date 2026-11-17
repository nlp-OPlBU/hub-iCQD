"""Kafka notification consumer; SQLite remains the source of task state."""
import asyncio
import json
import logging

from app.config import get_settings
from app.worker.executor import claim_task, process

logger = logging.getLogger(__name__)


def decode_task_uid(raw: bytes) -> str | None:
    """Validate the minimal versioned event contract before touching SQLite."""
    try:
        event = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(event, dict):
        return None
    if event.get("schema_version") != 1 or event.get("event_type") != "document.parse.requested":
        return None
    task_uid = event.get("task_uid")
    return task_uid if isinstance(task_uid, str) and task_uid else None


class KafkaTaskConsumer:
    def __init__(self):
        self.settings = get_settings()
        self.consumer = None
        self.active: set[asyncio.Task] = set()

    async def start(self):
        try:
            from aiokafka import AIOKafkaConsumer
        except ImportError as exc:
            raise RuntimeError("Install Kafka support with pip install -e '.[services]'") from exc
        self.consumer = AIOKafkaConsumer(
            self.settings.kafka_topic,
            bootstrap_servers=self.settings.kafka_bootstrap_servers,
            group_id=self.settings.kafka_consumer_group,
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=900_000,
            max_poll_records=20,
        )
        await self.consumer.start()

    async def stop(self):
        for task in self.active:
            task.cancel()
        if self.active:
            await asyncio.gather(*self.active, return_exceptions=True)
        if self.consumer:
            await self.consumer.stop()

    async def handle(self, message) -> bool:
        """Return true only when it is safe to commit this message's offset."""
        task_uid = decode_task_uid(message.value)
        if task_uid is None:
            logger.error("Discarding malformed or unsupported Kafka event at offset %s", message.offset)
            return True
        # Claim is a short SQLite transaction. A database exception must leave offset uncommitted.
        task = claim_task(self.settings.worker_id, task_uid)
        if task is None:
            with __import__("app.db", fromlist=["connect"]).connect() as db:
                exists = db.execute("SELECT status FROM parse_tasks WHERE task_uid=?", (task_uid,)).fetchone()
            if exists is None:
                logger.error("Kafka event references unknown task_uid=%s", task_uid)
            # Duplicate notifications and terminal/cancelled tasks are safely acknowledged.
            return True
        work = asyncio.create_task(process(task, self.settings.worker_id))
        self.active.add(work)
        work.add_done_callback(self.active.discard)
        return True

    async def run(self):
        await self.start()
        try:
            while True:
                # Pause delivery when all execution slots are occupied, but keep polling to heartbeat.
                partitions = self.consumer.assignment()
                if len(self.active) >= self.settings.worker_concurrency:
                    if partitions:
                        self.consumer.pause(*partitions)
                    await self.consumer.getmany(timeout_ms=1000, max_records=1)
                    self.active = {task for task in self.active if not task.done()}
                    if len(self.active) < self.settings.worker_concurrency and partitions:
                        self.consumer.resume(*partitions)
                    continue
                batches = await self.consumer.getmany(timeout_ms=1000, max_records=1)
                self.active = {task for task in self.active if not task.done()}
                for messages in batches.values():
                    for message in messages:
                        try:
                            if await self.handle(message):
                                # Commit only this partition's next offset after durable SQLite claim.
                                from aiokafka.structs import OffsetAndMetadata, TopicPartition
                                await self.consumer.commit({
                                    TopicPartition(message.topic, message.partition): OffsetAndMetadata(message.offset + 1, "")
                                })
                        except Exception:
                            logger.exception("Kafka message handling failed; offset remains uncommitted")
                            await asyncio.sleep(1)
                            break
        finally:
            await self.stop()


async def run_consumer():
    await KafkaTaskConsumer().run()
