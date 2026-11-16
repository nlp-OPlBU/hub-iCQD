"""文档解析 worker：消费 Kafka -> MinerU 解析 -> 切块向量化 -> 更新状态。

启动：
    cd backend
    python -m app.worker.parse_worker

消费语义：
    - enable_auto_commit=False，处理完一条手动 commit（至少一次）
    - 索引前先按 doc_id 删旧向量，重复消费不会产生重复数据（幂等）
    - 失败时清理半成品向量、状态置 failed，并把消息写入 DLQ
"""

import json
import logging
from pathlib import Path

from kafka import KafkaConsumer

from ..config import get_settings
from ..database import SessionLocal, init_db
from ..models import Document, DocumentStatus
from ..services.indexer import DocumentIndexer
from ..services.kafka_producer import parse_task_producer
from ..services.mineru_parser import parse_pdf

logger = logging.getLogger(__name__)


def process_message(payload: dict, indexer: DocumentIndexer, settings) -> None:
    document_id = payload.get("document_id")
    if document_id is None:
        logger.error("消息缺少 document_id，忽略: %s", payload)
        return
    document_id = int(document_id)

    with SessionLocal() as db:
        document = db.get(Document, document_id)
        if document is None:
            logger.warning("文档 %s 已被删除，跳过", document_id)
            return
        if document.status == DocumentStatus.COMPLETED:
            logger.info("文档 %s 已解析完成，跳过重复消息", document_id)
            return
        stored_path = document.stored_path
        original_name = document.original_name
        document.status = DocumentStatus.PARSING
        document.error_message = None
        db.commit()

    logger.info("开始解析文档 %s: %s", document_id, original_name)

    try:
        markdown_path = parse_pdf(stored_path, settings.processed_path, settings)
        parsed_dir = settings.processed_path / Path(stored_path).stem

        with SessionLocal() as db:
            document = db.get(Document, document_id)
            if document is None:
                logger.warning("文档 %s 在解析期间被删除，跳过入库", document_id)
                return
            document.markdown_path = str(markdown_path)
            document.parsed_dir = str(parsed_dir)
            db.commit()

        chunk_count = indexer.index_document(document_id, markdown_path, original_name)

        with SessionLocal() as db:
            document = db.get(Document, document_id)
            if document is None:
                # 删除接口可能和解析并发，兜底把向量清掉
                logger.warning("文档 %s 在入库期间被删除，清理向量", document_id)
                indexer.store.delete_by_doc(document_id)
                return
            document.status = DocumentStatus.COMPLETED
            document.chunk_count = chunk_count
            document.error_message = None
            db.commit()

        logger.info("文档 %s 解析完成，共 %s 个 chunk", document_id, chunk_count)

    except Exception as exc:
        logger.exception("文档 %s 解析失败", document_id)

        try:
            indexer.store.delete_by_doc(document_id)
        except Exception:
            logger.exception("清理失败文档的向量时出错")

        with SessionLocal() as db:
            document = db.get(Document, document_id)
            if document is not None:
                document.status = DocumentStatus.FAILED
                document.error_message = str(exc)[:4000]
                db.commit()

        parse_task_producer.publish_dlq(payload, str(exc))


def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    settings = get_settings()
    init_db()

    # MinerU 可能跑几分钟，必须放宽 max.poll.interval，否则会触发 rebalance 重复消费
    max_poll_interval_ms = max(
        settings.mineru_timeout_seconds * 1000 + 5 * 60 * 1000,
        30 * 60 * 1000,
    )

    consumer = KafkaConsumer(
        settings.kafka_parse_topic,
        bootstrap_servers=settings.kafka_servers_list,
        group_id=settings.kafka_group_id,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=1,
        max_poll_interval_ms=max_poll_interval_ms,
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
    )

    indexer = DocumentIndexer(settings=settings)
    logger.info(
        "Worker 已启动：topic=%s group=%s broker=%s",
        settings.kafka_parse_topic,
        settings.kafka_group_id,
        settings.kafka_bootstrap_servers,
    )

    try:
        for message in consumer:
            try:
                process_message(message.value, indexer, settings)
            except Exception:
                logger.exception("处理消息时发生未预期错误: %s", message.value)
            finally:
                # 无论成功失败都提交 offset，失败消息已进 DLQ，避免毒消息卡死队列
                try:
                    consumer.commit()
                except Exception:
                    logger.exception("提交 Kafka offset 失败")
    except KeyboardInterrupt:
        logger.info("收到中断信号，worker 退出")
    finally:
        consumer.close()
        parse_task_producer.close()
        logger.info("Worker 已停止")


if __name__ == "__main__":
    run()
