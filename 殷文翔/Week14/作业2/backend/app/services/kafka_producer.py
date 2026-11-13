"""Kafka publishing with an explicit broker acknowledgement.

The publisher is lazy so a worker or dispatcher can start while Kafka is down.
Callers must persist the outbox row before calling publish; a lost or failed
acknowledgement is retried from MySQL, never treated as a successful send.
"""
from __future__ import annotations

import json
import logging
from typing import Protocol

from app.core.config import Settings

logger = logging.getLogger(__name__)


class PublishError(Exception):
    """The broker did not confirm the record; the outbox event must stay pending."""


class EventPublisher(Protocol):
    def publish(self, topic: str, key: str, payload: dict, timeout: float | None = None) -> bool | None: ...

    def close(self) -> None: ...


class KafkaEventPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._producer = None

    def _get_producer(self):
        if self._producer is not None:
            return self._producer
        try:
            from kafka import KafkaProducer
        except ImportError as exc:  # pragma: no cover - deployment dependency check
            raise PublishError("Kafka client library is not installed") from exc
        try:
            self._producer = KafkaProducer(
                bootstrap_servers=[item.strip() for item in self.settings.kafka_bootstrap_servers.split(",") if item.strip()],
                acks="all",
                retries=0,
                linger_ms=0,
                bootstrap_timeout_ms=self.settings.kafka_publish_timeout_seconds * 1000,
                max_block_ms=self.settings.kafka_publish_timeout_seconds * 1000,
                request_timeout_ms=self.settings.kafka_publish_timeout_seconds * 1000,
                delivery_timeout_ms=self.settings.kafka_publish_timeout_seconds * 1000,
                key_serializer=lambda value: value.encode("utf-8"),
                value_serializer=lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            )
        except Exception as exc:
            self._producer = None
            raise PublishError("Kafka producer could not be created") from exc
        return self._producer

    def publish(self, topic: str, key: str, payload: dict, timeout: float | None = None) -> None:
        try:
            producer = self._get_producer()
            future = producer.send(topic, key=key, value=payload)
            # get() is the broker acknowledgement, not just a local enqueue result.
            future.get(timeout=timeout or self.settings.kafka_publish_timeout_seconds)
        except PublishError:
            raise
        except Exception as exc:
            self.close()
            raise PublishError("Kafka did not confirm the event") from exc

    def close(self) -> None:
        producer, self._producer = self._producer, None
        if producer is not None:
            try:
                producer.close(timeout=self.settings.kafka_publish_timeout_seconds)
            except Exception:
                logger.warning("Kafka producer close failed")


def create_publisher(settings: Settings) -> EventPublisher:
    return KafkaEventPublisher(settings)


# Readable alias for callers that describe the adapter by its transport role.
create_producer = create_publisher
