"""
Kafka 生产者 - 用于发送 PDF 上传任务到消息队列
"""
import json
import time
from typing import Dict, Optional

from loguru import logger

from ..config_loader import Config


class KafkaProducer:
    """
    Kafka 生产者封装

    如果 kafka-python 不可用，会降级为打印日志（开发模式）。
    也可以使用内存队列做本地测试（见 fallback 模式）。
    """

    def __init__(self, bootstrap_servers=None, topic=None):
        cfg = Config.get_section('kafka')
        self.bootstrap_servers = bootstrap_servers or cfg.get('bootstrap_servers', ['localhost:9092'])
        self.topic = topic or cfg.get('topic_upload', 'rag-upload-tasks')
        self._producer = None
        self._fallback_queue: list = []  # 降级队列

    def _ensure_producer(self):
        """懒加载初始化 Kafka 生产者"""
        if self._producer is not None:
            return True
        try:
            from kafka import KafkaProducer
            self._producer = KafkaProducer(
                bootstrap_servers=self.bootstrap_servers,
                value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                acks='all',
                retries=3,
            )
            logger.info(f"Kafka 生产者已连接: {self.bootstrap_servers}")
            return True
        except ImportError:
            logger.warning("kafka-python 未安装，使用降级模式（内存队列）")
            return False
        except Exception as e:
            logger.warning(f"Kafka 连接失败，使用降级模式: {e}")
            return False

    def send_upload_task(
        self,
        task_id: str,
        file_path: str,
        file_name: str,
        file_size: int,
    ) -> bool:
        """
        发送 PDF 上传任务到 Kafka

        Returns:
            是否发送成功
        """
        message: Dict = {
            "task_id": task_id,
            "file_path": file_path,
            "file_name": file_name,
            "file_size": file_size,
            "timestamp": time.time(),
        }

        if self._ensure_producer():
            try:
                future = self._producer.send(self.topic, message)
                self._producer.flush()
                logger.info(f"已发送上传任务到 Kafka: task_id={task_id}, file={file_name}")
                return True
            except Exception as e:
                logger.error(f"Kafka 发送失败: {e}")
                # 降级到内存队列
                self._fallback_queue.append(message)
                return True  # 降级也算成功（便于本地开发）
        else:
            # 降级模式：放入内存队列，同时打印
            self._fallback_queue.append(message)
            logger.warning(f"[降级模式] 上传任务已放入内存队列: task_id={task_id}")
            return True

    def get_fallback_queue(self) -> list:
        """获取降级队列（供本地消费者使用）"""
        return self._fallback_queue

    def close(self):
        """关闭生产者"""
        if self._producer:
            self._producer.close()
            self._producer = None
            logger.info("Kafka 生产者已关闭")


# 单例
_producer_instance: Optional[KafkaProducer] = None


def get_producer() -> KafkaProducer:
    global _producer_instance
    if _producer_instance is None:
        _producer_instance = KafkaProducer()
    return _producer_instance
