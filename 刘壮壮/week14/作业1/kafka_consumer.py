"""订阅 Kafka 消息并演示写入 Redis 对话历史。"""

import json
import os

from dotenv import load_dotenv
from kafka import KafkaConsumer

from kafka_common import BOOTSTRAP_SERVERS, GROUP_ID, TOPIC
from redis_chat import ChatHistory, create_redis_client

load_dotenv()


def main() -> None:
    redis_client = create_redis_client()
    redis_client.ping()
    history = ChatHistory(redis_client, ttl=int(os.getenv("REDIS_TTL_SECONDS", "86400")))
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=BOOTSTRAP_SERVERS.split(","),
        group_id=GROUP_ID,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        value_deserializer=lambda value: json.loads(value.decode("utf-8")),
        key_deserializer=lambda key: key.decode("utf-8") if key else None,
    )
    print(f"正在监听 topic={TOPIC}，按 Ctrl+C 退出。")
    try:
        for message in consumer:
            event = message.value
            conversation_id = event["conversation_id"]
            content = event["content"]
            history.add_message(conversation_id, "user", content)
            print(f"收到 partition={message.partition} offset={message.offset}: {event}")
            consumer.commit()
    except KeyboardInterrupt:
        print("消费者停止。")
    finally:
        consumer.close()
        redis_client.close()


if __name__ == "__main__":
    main()
