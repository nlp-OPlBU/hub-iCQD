"""Kafka 客户端封装：topic 管理、JSON 生产者、消费工厂。

消费参数全部来自配置，不靠命令行覆盖，保证测试与生产一致。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Iterable

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

from app.core.config import get_settings
from app.core.logging import get_logger, log_event

logger = get_logger("app.infra.kafka")

_producer: KafkaProducer | None = None


def _bootstrap() -> str:
    return get_settings().kafka.bootstrap_servers


def _topic_specs() -> list[NewTopic]:
    cfg = get_settings().kafka
    return [
        NewTopic(
            name=cfg.topic,
            num_partitions=cfg.partitions,
            replication_factor=1,
            topic_configs={"retention.ms": str(cfg.retention_ms)},
        ),
        NewTopic(
            name=cfg.dlq_topic,
            num_partitions=1,
            replication_factor=1,
            topic_configs={"retention.ms": str(cfg.dlq_retention_ms)},
        ),
    ]


def get_admin(timeout_ms: int | None = None) -> KafkaAdminClient:
    return KafkaAdminClient(
        bootstrap_servers=_bootstrap(),
        client_id="rag-admin",
        request_timeout_ms=timeout_ms or get_settings().kafka.request_timeout_ms,
    )


def ensure_topics() -> dict[str, str]:
    """幂等建 topic，返回 {topic: created|exists}。"""
    admin = get_admin()
    result: dict[str, str] = {}
    try:
        for spec in _topic_specs():
            try:
                admin.create_topics([spec])
                result[spec.name] = "created"
            except TopicAlreadyExistsError:
                result[spec.name] = "exists"
    finally:
        admin.close()
    log_event(logger, "topics_ready", **result)
    return result


def list_topics() -> list[str]:
    admin = get_admin()
    try:
        return sorted(admin.list_topics())
    finally:
        admin.close()


def ping_kafka() -> None:
    list_topics()


def _serialize(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def _serialize_key(value: str | None) -> bytes | None:
    return value.encode("utf-8") if value is not None else None


def create_producer() -> KafkaProducer:
    settings = get_settings().kafka
    return KafkaProducer(
        bootstrap_servers=settings.bootstrap_servers,
        client_id="rag-producer",
        value_serializer=_serialize,
        key_serializer=_serialize_key,
        acks="all",
        request_timeout_ms=settings.request_timeout_ms,
    )


def get_producer() -> KafkaProducer:
    global _producer
    if _producer is None:
        _producer = create_producer()
    return _producer


def publish(topic: str, key: str, payload: dict[str, Any]) -> None:
    """同步等待 broker 确认，保证落盘后接口才返回 202。"""
    metadata = get_producer().send(topic, key=key, value=payload).get(
        timeout=get_settings().kafka.request_timeout_ms / 1000
    )
    log_event(
        logger,
        "kafka_published",
        topic=topic,
        partition=metadata.partition,
        offset=metadata.offset,
        key=key,
    )


def create_consumer(topics: Iterable[str] | None = None, group_id: str | None = None) -> KafkaConsumer:
    cfg = get_settings().kafka
    return KafkaConsumer(
        *(list(topics) if topics else [cfg.topic]),
        bootstrap_servers=cfg.bootstrap_servers,
        group_id=group_id or cfg.group_id,
        client_id="rag-ingest-worker",
        enable_auto_commit=False,
        auto_offset_reset=cfg.auto_offset_reset,
        max_poll_records=cfg.max_poll_records,
        max_poll_interval_ms=cfg.max_poll_interval_ms,
        request_timeout_ms=cfg.request_timeout_ms,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
    )


def close_producer() -> None:
    global _producer
    if _producer is not None:
        try:
            _producer.flush(timeout=5)
            _producer.close(timeout=5)
        except Exception as exc:
            logger.warning("producer close failed: %s", exc)
    _producer = None
