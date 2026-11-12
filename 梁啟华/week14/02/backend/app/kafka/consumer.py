"""
Kafka 消费者 - 消费 PDF 上传任务，调用解析服务处理

支持两种模式：
1. 真实 Kafka 消费（生产环境）
2. 从降级内存队列消费（本地开发/测试）
"""
import asyncio
import json
import threading
from pathlib import Path
from typing import Callable, Dict, Optional

from loguru import logger

from ..config_loader import Config
from ..models import DocumentStatus
from ..db import get_db


class KafkaConsumer:
    """Kafka 消费者封装"""

    def __init__(self, bootstrap_servers=None, topic=None, group_id=None):
        cfg = Config.get_section('kafka')
        self.bootstrap_servers = bootstrap_servers or cfg.get('bootstrap_servers', ['localhost:9092'])
        self.topic = topic or cfg.get('topic_upload', 'rag-upload-tasks')
        self.group_id = group_id or cfg.get('consumer_group', 'rag-parser-group')
        self._consumer = None
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._is_fallback = False

    def _ensure_consumer(self) -> bool:
        """初始化 Kafka 消费者"""
        if self._consumer is not None:
            return True
        try:
            from kafka import KafkaConsumer
            self._consumer = KafkaConsumer(
                self.topic,
                bootstrap_servers=self.bootstrap_servers,
                group_id=self.group_id,
                auto_offset_reset='earliest',
                value_deserializer=lambda m: json.loads(m.decode('utf-8')),
                enable_auto_commit=False,
            )
            self._is_fallback = False
            logger.info(f"Kafka 消费者已连接: topic={self.topic}, group={self.group_id}")
            return True
        except ImportError:
            logger.warning("kafka-python 未安装，使用降级内存队列模式")
            self._is_fallback = True
            return False
        except Exception as e:
            logger.warning(f"Kafka 连接失败，使用降级模式: {e}")
            self._is_fallback = True
            return False

    def start(self, handler: Callable[[Dict], None]):
        """
        启动消费者（后台线程）

        Args:
            handler: 处理单条消息的回调函数，签名 handler(message_dict)
        """
        self._running = True

        if self._ensure_consumer() and not self._is_fallback:
            self._thread = threading.Thread(
                target=self._kafka_loop,
                args=(handler,),
                daemon=True,
                name="kafka-consumer"
            )
        else:
            self._thread = threading.Thread(
                target=self._fallback_loop,
                args=(handler,),
                daemon=True,
                name="fallback-consumer"
            )

        self._thread.start()
        logger.info("消费者已启动")

    def stop(self):
        """停止消费者"""
        self._running = False
        if self._consumer:
            self._consumer.close()
            self._consumer = None
        logger.info("消费者已停止")

    def _kafka_loop(self, handler: Callable):
        """真实 Kafka 消费循环"""
        try:
            for message in self._consumer:
                if not self._running:
                    break
                try:
                    handler(message.value)
                    self._consumer.commit()
                except Exception as e:
                    logger.error(f"处理消息失败: {e}")
        except Exception as e:
            logger.error(f"Kafka 消费循环异常: {e}")

    def _fallback_loop(self, handler: Callable):
        """降级模式：周期性检查内存队列"""
        from .producer import get_producer
        producer = get_producer()
        poll_interval = 2  # 秒

        while self._running:
            try:
                queue = producer.get_fallback_queue()
                if queue:
                    message = queue.pop(0)
                    logger.info(f"[降级模式] 处理队列消息: task_id={message.get('task_id')}")
                    try:
                        handler(message)
                    except Exception as e:
                        logger.error(f"[降级模式] 处理失败: {e}")
                else:
                    # 没有任务时检查数据库中 pending 状态的任务（兜底）
                    asyncio.run(self._process_pending_tasks(handler))

                time.sleep(poll_interval)
            except Exception as e:
                logger.error(f"[降级模式] 循环异常: {e}")
                time.sleep(poll_interval)

    async def _process_pending_tasks(self, handler: Callable):
        """兜底：扫描数据库中 pending 状态的任务"""
        db = get_db()
        pending_docs = await db.list_documents(DocumentStatus.PENDING)
        for doc in pending_docs:
            message = {
                "task_id": doc['task_id'],
                "file_path": doc['file_path'],
                "file_name": doc['file_name'],
                "file_size": doc['file_size'],
            }
            handler(message)

    @property
    def is_fallback(self) -> bool:
        return self._is_fallback


import time  # noqa: E402
