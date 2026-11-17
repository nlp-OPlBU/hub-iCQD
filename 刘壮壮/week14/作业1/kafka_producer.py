"""从标准输入读取文本并发送到 Kafka。"""

import json
import uuid
from datetime import datetime, timezone

from kafka import KafkaProducer
from kafka.errors import KafkaError

from kafka_common import BOOTSTRAP_SERVERS, TOPIC


def main() -> None:
    producer = KafkaProducer(
        bootstrap_servers=BOOTSTRAP_SERVERS.split(","),
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
        key_serializer=lambda key: key.encode("utf-8"),
        acks="all",
        retries=3,
    )
    conversation_id = input("会话 ID（直接回车使用 demo）：").strip() or "demo"
    print("输入消息发送到 Kafka，输入 /quit 退出。")
    try:
        while True:
            content = input("消息：").strip()
            if content == "/quit":
                break
            if not content:
                continue
            event = {
                "event_id": str(uuid.uuid4()),
                "conversation_id": conversation_id,
                "content": content,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            try:
                metadata = producer.send(TOPIC, key=conversation_id, value=event).get(timeout=10)
                print(f"已发送 topic={metadata.topic} partition={metadata.partition} offset={metadata.offset}")
            except KafkaError as exc:
                print(f"发送失败：{exc}")
    finally:
        producer.flush()
        producer.close()


if __name__ == "__main__":
    main()
