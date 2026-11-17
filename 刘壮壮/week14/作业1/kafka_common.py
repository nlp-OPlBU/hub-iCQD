"""Kafka 示例共享配置。"""

import os

from dotenv import load_dotenv

load_dotenv()

BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
TOPIC = os.getenv("KAFKA_TOPIC", "chat-messages")
GROUP_ID = os.getenv("KAFKA_GROUP_ID", "chat-message-demo")
