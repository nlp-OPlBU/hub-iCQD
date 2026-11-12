"""Kafka 实践：用 Kafka 在两个角色之间传递聊天消息。

流程：
  客户端(send) --chat-messages--> 机器人(bot) --chat-replies--> 客户端(client / demo)

用法：
  python3 kafka_chat_pipeline.py demo                        # 一次跑通生产+消费
  python3 kafka_chat_pipeline.py bot                         # 常驻机器人（终端 A）
  python3 kafka_chat_pipeline.py client                      # 常驻收回复（终端 B）
  python3 kafka_chat_pipeline.py send --session s1 "你好"    # 生产一条消息
"""

import argparse
import json
import os
import sys
import threading
import time
import uuid
import warnings

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import TopicAlreadyExistsError

# kafka-python 3.x 对 lambda 序列化器会发 DeprecationWarning，这里屏蔽掉
warnings.filterwarnings("ignore", message=".*does not implement kafka.serializer.*")

BOOTSTRAP = os.getenv("KAFKA_BOOTSTRAP", "localhost:9092")
TOPIC_REQUEST = "chat-messages"
TOPIC_REPLY = "chat-replies"


def ensure_topics() -> None:
    """Kafka 关闭了自动建 topic，这里显式创建（已存在则忽略）。"""
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP, request_timeout_ms=10000)
    try:
        admin.create_topics(
            [
                NewTopic(name=TOPIC_REQUEST, num_partitions=1, replication_factor=1),
                NewTopic(name=TOPIC_REPLY, num_partitions=1, replication_factor=1),
            ]
        )
        print(f"已创建 topic: {TOPIC_REQUEST}, {TOPIC_REPLY}")
    except TopicAlreadyExistsError:
        pass
    finally:
        admin.close()


def build_producer() -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=BOOTSTRAP,
        key_serializer=lambda k: k.encode("utf-8"),
        value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
        acks="all",
    )


def send_message(producer: KafkaProducer, session: str, text: str) -> dict:
    message = {
        "msg_id": uuid.uuid4().hex,
        "session": session,
        "text": text,
        "ts": time.time(),
    }
    # key 用 session，保证同一会话的消息进入同一分区、保持顺序
    producer.send(TOPIC_REQUEST, key=session, value=message).get(timeout=10)
    print(f"  -> 已发送到 {TOPIC_REQUEST}: session={session} text={text}")
    return message


def print_reply(reply: dict) -> None:
    print(f"  <- 收到回复[{reply['session']}]: {reply['text']}", flush=True)


def poll_replies(consumer: KafkaConsumer, timeout_ms: int = 1000) -> list[dict]:
    replies = []
    records = consumer.poll(timeout_ms=timeout_ms)
    for batch in records.values():
        for record in batch:
            replies.append(record.value)
    return replies


def run_bot(stop_event: threading.Event, group_id: str = "chat-bot") -> None:
    """消费请求 -> 生成回复 -> 发到回复 topic。"""
    consumer = KafkaConsumer(
        TOPIC_REQUEST,
        bootstrap_servers=BOOTSTRAP,
        group_id=group_id,
        auto_offset_reset="latest",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    producer = build_producer()
    print(f"机器人已启动，监听 {TOPIC_REQUEST} (group={group_id})")
    try:
        while not stop_event.is_set():
            for reply in poll_replies(consumer, timeout_ms=500):
                text = (
                    f"[机器人] 会话 {reply['session']} 收到「{reply['text']}」"
                    f"，消息号 {reply['msg_id'][:8]}"
                )
                response = {
                    "msg_id": uuid.uuid4().hex,
                    "reply_to": reply["msg_id"],
                    "session": reply["session"],
                    "text": text,
                    "ts": time.time(),
                }
                producer.send(TOPIC_REPLY, key=reply["session"], value=response).get(timeout=10)
                print(text, flush=True)
    finally:
        producer.close()
        consumer.close()
        print("机器人已停止", flush=True)


def cmd_bot() -> None:
    stop = threading.Event()
    try:
        run_bot(stop)
    except KeyboardInterrupt:
        stop.set()


def cmd_client(group_id: str = "chat-client") -> None:
    consumer = KafkaConsumer(
        TOPIC_REPLY,
        bootstrap_servers=BOOTSTRAP,
        group_id=group_id,
        auto_offset_reset="latest",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    print(f"客户端监听 {TOPIC_REPLY}，Ctrl+C 退出", flush=True)
    try:
        while True:
            for reply in poll_replies(consumer, timeout_ms=1000):
                print_reply(reply)
    except KeyboardInterrupt:
        pass
    finally:
        consumer.close()


def cmd_send(session: str, text: str | None) -> None:
    producer = build_producer()
    try:
        if text:
            send_message(producer, session, text)
        else:
            print("输入消息回车发送，空行退出：")
            while True:
                line = input("> ").strip()
                if not line:
                    break
                send_message(producer, session, line)
    finally:
        producer.close()


def cmd_demo() -> None:
    """自动演示：后台启动机器人，发送 3 条消息并等待回复。"""
    ensure_topics()
    stop = threading.Event()
    bot = threading.Thread(
        target=run_bot, args=(stop, f"chat-bot-demo-{uuid.uuid4().hex[:6]}"), daemon=True
    )
    bot.start()

    # 先订阅回复 topic 再发消息，避免 auto_offset_reset=latest 漏掉早到的回复
    consumer = KafkaConsumer(
        TOPIC_REPLY,
        bootstrap_servers=BOOTSTRAP,
        group_id=f"chat-client-demo-{uuid.uuid4().hex[:6]}",
        auto_offset_reset="latest",
        value_deserializer=lambda b: json.loads(b.decode("utf-8")),
    )
    consumer.poll(timeout_ms=2000)  # 首次 poll 才会加入消费组并把位置定到末尾
    time.sleep(1)  # 等机器人消费者也完成分配

    producer = build_producer()
    sent = {}
    try:
        print("发送 3 条测试消息：")
        for text in ["你好", "Redis 历史记录写完了", "再帮我看看 Kafka"]:
            msg = send_message(producer, "demo-session", text)
            sent[msg["msg_id"]] = msg
    finally:
        producer.close()

    received = set()
    deadline = time.time() + 20
    print("等待回复：")
    try:
        while len(received) < len(sent) and time.time() < deadline:
            for reply in poll_replies(consumer, timeout_ms=1000):
                if reply["reply_to"] in sent and reply["reply_to"] not in received:
                    received.add(reply["reply_to"])
                    print_reply(reply)
    finally:
        consumer.close()
        stop.set()
        bot.join(timeout=5)

    if len(received) == len(sent):
        print(f"\n演示完成：{len(received)}/{len(sent)} 条消息完成往返。")
    else:
        print(f"\n超时：只收到 {len(received)}/{len(sent)} 条回复。", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="用 Kafka 传递聊天消息")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="自动演示生产/消费全流程")
    sub.add_parser("bot", help="启动机器人消费者")
    sub.add_parser("client", help="监听回复 topic")
    send = sub.add_parser("send", help="发送消息")
    send.add_argument("--session", default="default")
    send.add_argument("text", nargs="?")

    args = parser.parse_args()
    if args.command == "demo":
        cmd_demo()
    elif args.command == "bot":
        cmd_bot()
    elif args.command == "client":
        cmd_client()
    elif args.command == "send":
        cmd_send(args.session, args.text)


if __name__ == "__main__":
    main()
