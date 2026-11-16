"""Kafka 生产者：上传接口只负责“投递解析任务”，解析由 worker 异步完成。"""

import json
import logging
import threading
from datetime import datetime, timezone

from kafka import KafkaProducer

from ..config import Settings, get_settings

logger = logging.getLogger(__name__)


class ParseTaskProducer:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._producer: KafkaProducer | None = None
        self._lock = threading.Lock()

    def _get_producer(self) -> KafkaProducer:
        if self._producer is None:
            with self._lock:
                if self._producer is None:
                    self._producer = KafkaProducer(
                        bootstrap_servers=self.settings.kafka_servers_list,
                        key_serializer=lambda k: str(k).encode("utf-8"),
                        value_serializer=lambda v: json.dumps(
                            v, ensure_ascii=False
                        ).encode("utf-8"),
                        acks="all",
                        retries=3,
                        linger_ms=10,
                        max_block_ms=5000,
                    )
        return self._producer

    def publish_parse_task(
        self,
        *,
        document_id: int,
        original_name: str,
        stored_path: str,
    ) -> None:
        payload = {
            "task": "parse_document",
            "document_id": document_id,
            "original_name": original_name,
            "stored_path": stored_path,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }
        # 以 doc_id 作为 key，同一文档的消息进入同一分区，保证顺序
        future = self._get_producer().send(
            self.settings.kafka_parse_topic, key=document_id, value=payload
        )
        # 等 broker 确认，投递失败立刻抛给接口层
        future.get(timeout=10)

    def publish_dlq(self, payload: dict, error: str) -> None:
        try:
            future = self._get_producer().send(
                self.settings.kafka_dlq_topic,
                key=payload.get("document_id"),
                value={
                    **payload,
                    "error": error[:4000],
                    "failed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            future.get(timeout=10)
        except Exception:  # DLQ 写失败不能影响主流程
            logger.exception("写入 DLQ 失败")

    def close(self) -> None:
        if self._producer is not None:
            try:
                self._producer.flush(timeout=5)
                self._producer.close(timeout=5)
            finally:
                self._producer = None


parse_task_producer = ParseTaskProducer()


def close_producer() -> None:
    parse_task_producer.close()
