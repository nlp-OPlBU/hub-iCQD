import argparse
import json

from kafka import KafkaAdminClient, KafkaConsumer, KafkaProducer
from kafka.admin import NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError

BOOTSTRAP_SERVERS = "127.0.0.1:9092"
TOPIC = "hw1-orders"
GROUP_ID = "hw1-practice-group"
PARTITIONS = 3


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Kafka 练习程序")
    parser.add_argument("action", choices=["create", "produce", "consume"], help="要执行的操作")
    parser.add_argument("--bootstrap", default=BOOTSTRAP_SERVERS, help=f"Kafka 地址（默认 {BOOTSTRAP_SERVERS}）")
    parser.add_argument("--topic", default=TOPIC, help=f"topic 名称（默认 {TOPIC}）")
    parser.add_argument(
        "-n",
        "--count",
        type=int,
        default=10,
        help="produce：发送的条数；consume：最多消费的条数，0 表示不限制（默认 10）",
    )
    parser.add_argument("--group", default=GROUP_ID, help=f"消费组 id（默认 {GROUP_ID}）")
    return parser


def create_topic(bootstrap: str, topic: str) -> None:
    """创建 topic。分区数决定了消息可以并行消费的消费者数量上限。"""
    admin = KafkaAdminClient(
        bootstrap_servers=bootstrap,
        client_id="hw1-admin",
        request_timeout_ms=5000,
    )
    try:
        admin.create_topics(
            [NewTopic(name=topic, num_partitions=PARTITIONS, replication_factor=1)]
        )
        print(f"已创建 topic：{topic}（{PARTITIONS} 个分区）")
    except TopicAlreadyExistsError:
        print(f"topic {topic} 已存在，无需重复创建")
    finally:
        admin.close()


def produce_messages(bootstrap: str, topic: str, count: int) -> None:
    """生产消息：key 相同的消息会被分到同一个分区。"""
    producer = KafkaProducer(
        bootstrap_servers=bootstrap,
        client_id="hw1-producer",
        acks="all",  # 等所有副本确认后才算发送成功
        key_serializer=lambda key: key.encode("utf-8"),
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
    )
    try:
        for i in range(1, count + 1):
            # 轮流使用 3 个用户，这样同一个用户的消息会进入同一个分区
            user = f"user-{(i - 1) % 3 + 1}"
            order = {"order_id": i, "user": user, "amount": i * 10}
            metadata = producer.send(topic, key=user, value=order).get(timeout=10)
            print(
                f"发送 {order} -> 分区 {metadata.partition}，偏移量 {metadata.offset}"
            )
        producer.flush()
        print(f"共发送 {count} 条消息")
    finally:
        producer.close()


def consume_messages(bootstrap: str, topic: str, group_id: str, max_count: int) -> None:
    """消费消息。同一个消费组会记住已经读到的位置（offset）。"""
    consumer = KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        client_id="hw1-consumer",
        group_id=group_id,
        auto_offset_reset="earliest",  # 没有历史 offset 时从头开始读
        enable_auto_commit=True,       # 自动提交 offset，便于重复练习
        consumer_timeout_ms=30000,     # 30 秒没有新消息就结束循环
        key_deserializer=lambda raw: raw.decode("utf-8") if raw is not None else None,
        value_deserializer=lambda raw: json.loads(raw.decode("utf-8")),
    )
    print(f"开始消费 topic={topic}，消费组={group_id}")
    print("（等待消息中，30 秒无新消息会自动退出；按 Ctrl+C 可提前停止）")

    received = 0
    try:
        for message in consumer:
            print(
                f"收到 分区={message.partition} 偏移量={message.offset} "
                f"key={message.key} value={message.value}"
            )
            received += 1
            if max_count and received >= max_count:
                break
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止消费")
    finally:
        consumer.close()

    print(f"本次共消费 {received} 条消息")


def main() -> int:
    args = build_parser().parse_args()

    try:
        if args.action == "create":
            create_topic(args.bootstrap, args.topic)
        elif args.action == "produce":
            produce_messages(args.bootstrap, args.topic, args.count)
        else:
            consume_messages(args.bootstrap, args.topic, args.group, args.count)
    except KafkaError as exc:
        print(f"Kafka 操作失败：{exc}")
        print(f"请确认 Kafka 服务已启动，并且地址 {args.bootstrap} 可以访问。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
