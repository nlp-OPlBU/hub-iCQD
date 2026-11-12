"""Minimal Kafka consumer adapter with manual offset commits.

The business worker controls commit timing: an offset is committed only after
the task result, a retry schedule, or a terminal failure has been persisted.
"""
from __future__ import annotations

from typing import Any, Protocol

from app.core.config import Settings


class ConsumerRecord(Protocol):
    topic: str
    partition: int
    offset: int
    key: bytes | None
    value: bytes


class TaskConsumer(Protocol):
    def poll(self, timeout: float) -> ConsumerRecord | None: ...

    def commit(self) -> None: ...

    def rewind(self, record: ConsumerRecord) -> None: ...

    def close(self) -> None: ...


class KafkaTaskConsumer:
    def __init__(self, settings: Settings, client_id: str | None = None):
        from kafka import KafkaConsumer, TopicPartition

        self._topic_partition = TopicPartition
        self._consumer = KafkaConsumer(
            settings.kafka_pdf_topic,
            bootstrap_servers=[item.strip() for item in settings.kafka_bootstrap_servers.split(",") if item.strip()],
            group_id=settings.kafka_consumer_group,
            client_id=client_id or "multimodal-rag-pdf-worker",
            enable_auto_commit=False,
            auto_offset_reset="earliest",
            max_poll_interval_ms=settings.kafka_max_poll_interval_ms,
            consumer_timeout_ms=settings.kafka_consumer_poll_seconds * 1000,
        )

    def poll(self, timeout: float) -> Any | None:
        batches = self._consumer.poll(timeout_ms=int(timeout * 1000), max_records=1)
        for records in batches.values():
            if records:
                return records[0]
        return None

    def commit(self) -> None:
        self._consumer.commit()

    def rewind(self, record: Any) -> None:
        # Kafka offsets are position markers; seeking to the failed record keeps
        # the event ahead of the next poll without marking it processed.
        self._consumer.seek(self._topic_partition(record.topic, record.partition), record.offset)

    def close(self) -> None:
        self._consumer.close(autocommit=False)


def create_consumer(settings: Settings, client_id: str | None = None) -> TaskConsumer:
    return KafkaTaskConsumer(settings, client_id=client_id)
