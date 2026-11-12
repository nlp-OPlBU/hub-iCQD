#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Kafka 传递聊天消息 —— Kafka 生产者 / 消费者 / 消费组实战

场景：一个聊天系统拆成两类进程

    ┌──────────┐  发消息（key=session_id）   ┌──────────────────────┐
    │ 生产者   │ ──────────────────────────▶ │ week14.chat.messages │ 3 个分区
    └──────────┘                             └──────────┬───────────┘
                                                       │
                        ┌──────────────────────────────┼──────────────────────────┐
                        │ 消费组 reply-workers（2 个消费者）                          │ 消费组 audit（1 个消费者）
                        ▼                                                          ▼
                  生成回复 → 写 replies topic                                  只做审计统计
                                                        ┌──────────────────────┐
                                                        │ week14.chat.replies  │
                                                        └──────────┬───────────┘
                                                                   │ 消费组 reply-printers
                                                                   ▼
                                                               打印回复

想练的几件事：
  1. **topic 与分区**：消息按 key 分区，同一个 session 的消息落在同一个分区 → 分区内有序；
     不同 key 可以并行到不同分区。
  2. **消费组**：同一组内的消费者分摊分区（每条消息只被组内一个成员处理，实现「扩缩容」）；
     不同组各收一份（同一条消息可以被多个业务各自消费）。
  3. **offset 与提交**：手动 commit，消费进度落在 Kafka 里，进程重启后从上次位置继续。
  4. **收发解耦**：生产者不关心谁消费、有几个消费者；消费者挂了不影响生产者。

运行：
    python kafka_chat_bus.py                 # 端到端演示（下面三段一起跑）
    python kafka_chat_bus.py produce         # 只发消息
    python kafka_chat_bus.py consume         # 只消费（Ctrl-C 退出）
    python kafka_chat_bus.py consume --topic week14.chat.replies --group week14-observer-replies

默认连 localhost:9092，可用 --bootstrap 或环境变量 KAFKA_BOOTSTRAP_SERVERS 覆盖。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple

from kafka import KafkaConsumer, KafkaProducer
from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import KafkaError, TopicAlreadyExistsError

TOPIC_IN = "week14.chat.messages"
TOPIC_OUT = "week14.chat.replies"
NUM_PARTITIONS = 3
DEFAULT_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")

# 演示用的会话和消息：3 个会话 × 3 句，方便观察「同 key 同分区、不同 key 可并行」
SESSIONS = ["s-1001", "s-1002", "s-1003"]
UTTERANCES = [
    "你好，我想了解一下 Kafka 的分区",
    "同一个会话的消息会乱序吗？",
    "那消费组又是做什么的？",
]


# ----------------------------- 基础工具 -----------------------------
def json_serializer(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def json_deserializer(raw: Optional[bytes]) -> Any:
    return json.loads(raw.decode("utf-8")) if raw else None


def text_key_serializer(key: Optional[str]) -> Optional[bytes]:
    return key.encode("utf-8") if key is not None else None


def text_key_deserializer(raw: Optional[bytes]) -> Optional[str]:
    return raw.decode("utf-8") if raw else None


class Counters:
    """线程安全的计数器，用来汇总各消费者处理了多少条。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, int] = {}

    def inc(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._data[key] = self._data.get(key, 0) + n

    def get(self, key: str) -> int:
        with self._lock:
            return self._data.get(key, 0)

    def snapshot(self) -> Dict[str, int]:
        with self._lock:
            return dict(self._data)


class SeenOrder:
    """记录每个会话被消费到的 seq 顺序，用来验证「同 key 分区内有序」。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._data: Dict[str, List[int]] = {}

    def add(self, key: str, seq: int) -> None:
        with self._lock:
            self._data.setdefault(key, []).append(seq)

    def snapshot(self) -> Dict[str, List[int]]:
        with self._lock:
            return {k: list(v) for k, v in self._data.items()}


def connect_admin(bootstrap: str) -> KafkaAdminClient:
    admin = KafkaAdminClient(
        bootstrap_servers=bootstrap, client_id="week14-admin", request_timeout_ms=5000
    )
    admin.list_topics()  # 真正发一次请求，连不上会在这里抛异常
    return admin


def ensure_topics(admin: KafkaAdminClient, topics: List[str], partitions: int = NUM_PARTITIONS) -> None:
    existing = set(admin.list_topics())
    to_create = [NewTopic(name=t, num_partitions=partitions, replication_factor=1) for t in topics if t not in existing]
    if not to_create:
        print(f"topic 已存在：{topics}")
        return
    try:
        admin.create_topics(to_create)
    except TopicAlreadyExistsError:
        pass
    print(f"已创建 topic：{[t.name for t in to_create]}（各 {partitions} 分区，副本 1）")


def make_producer(bootstrap: str) -> KafkaProducer:
    return KafkaProducer(
        bootstrap_servers=bootstrap,
        client_id="week14-producer",
        acks="all",                 # 等所有 ISR 确认，练习环境里更直观地拿到分区/offset
        retries=3,
        linger_ms=5,
        key_serializer=text_key_serializer,
        value_serializer=json_serializer,
    )


def make_consumer(bootstrap: str, topic: str, group: str, auto_commit: bool = False) -> KafkaConsumer:
    return KafkaConsumer(
        topic,
        bootstrap_servers=bootstrap,
        group_id=group,
        client_id=f"week14-{group}-{uuid.uuid4().hex[:4]}",
        enable_auto_commit=auto_commit,
        auto_offset_reset="earliest",   # 新消费组从头读起，演示时保证能看到历史
        max_poll_records=50,
        key_deserializer=text_key_deserializer,
        value_deserializer=json_deserializer,
    )


def fake_reply(content: str, seq: int, worker: int) -> str:
    return f"（worker{worker} 自动回复）你第 {seq} 句说的是「{content[:18]}…」，收到。"


# ----------------------------- 消费者线程 -----------------------------
def consume_loop(
    *,
    bootstrap: str,
    topic: str,
    group: str,
    label: str,
    stop: threading.Event,
    ready: threading.Event,
    handler: Callable[[Any], None],
    auto_commit: bool = False,
) -> None:
    """一个消费者 = 一个线程 + 自己的 consumer 实例（KafkaConsumer 不是线程安全的）。"""
    consumer = make_consumer(bootstrap, topic, group, auto_commit=auto_commit)
    print(f"[{label}] 已加入消费组「{group}」，订阅 {topic}，等待分区分配…")
    announced = False
    try:
        while not stop.is_set():
            batch = consumer.poll(timeout_ms=300)
            if not batch:
                if not announced and consumer.assignment():
                    print(f"[{label}] 分到分区 {sorted(p.partition for p in consumer.assignment())}")
                    announced = True
                    ready.set()
                continue
            for _tp, records in batch.items():
                for record in records:
                    handler(record)
            if not auto_commit:
                consumer.commit()   # 处理完一批再手动提交 offset
            if not announced and consumer.assignment():
                print(f"[{label}] 分到分区 {sorted(p.partition for p in consumer.assignment())}")
                announced = True
                ready.set()
    except KafkaError as exc:            # broker 挂掉等
        print(f"[{label}] 消费出错：{exc}")
    finally:
        consumer.close()
        print(f"[{label}] 已关闭")


# ----------------------------- 三种模式 -----------------------------
def run_demo(bootstrap: str) -> int:
    run_id = uuid.uuid4().hex[:6]
    total = len(SESSIONS) * len(UTTERANCES)
    stop = threading.Event()
    counters = Counters()
    seen = SeenOrder()
    ready: Dict[str, threading.Event] = {
        name: threading.Event() for name in ["reply-1", "reply-2", "audit", "printer"]
    }
    partition_owner: Dict[str, int] = {}
    owner_lock = threading.Lock()
    replies: List[Dict[str, Any]] = []
    replies_lock = threading.Lock()

    print(f"本次演示 run_id={run_id}，共发送 {total} 条消息\n")

    admin = connect_admin(bootstrap)
    ensure_topics(admin, [TOPIC_IN, TOPIC_OUT])
    admin.close()

    producer = make_producer(bootstrap)

    # 回复 worker：消费进来的一条消息 → 生成回复 → 写到 replies topic
    def make_reply_handler(worker_no: int) -> Callable[[Any], None]:
        def handle_in(record: Any) -> None:
            value = record.value
            if value.get("run_id") != run_id:
                return                      # 忽略上一次演示残留的消息（offset 照样提交）
            seq = value["seq"]
            session_id = record.key
            reply = {
                "run_id": run_id,
                "session_id": session_id,
                "seq": seq,
                "in_partition": record.partition,
                "reply": fake_reply(value["content"], seq, worker_no),
                "ts": time.time(),
            }
            meta = producer.send(TOPIC_OUT, key=session_id, value=reply).get(timeout=10)
            counters.inc(f"reply-worker-{worker_no}")
            counters.inc("reply_total")
            with owner_lock:
                partition_owner[session_id] = worker_no
            seen.add(session_id, seq)
            with replies_lock:
                replies.append(reply)
            print(
                f"[回复 worker{worker_no}] {session_id} #{seq} 来自分区 {record.partition} "
                f"→ 回复写入分区 {meta.partition}（offset {meta.offset}）"
            )

        return handle_in

    # 审计消费者：另一个消费组，同一条消息也能收到一份，只做统计
    def handle_audit(record: Any) -> None:
        value = record.value
        if value.get("run_id") != run_id:
            return
        counters.inc("audit")
        print(f"[审计] 围观到 {record.key} #{value['seq']}（分区 {record.partition}，offset {record.offset}）")

    # 回复打印者：订阅 replies topic，验证下游还有一层解耦
    def handle_reply(record: Any) -> None:
        value = record.value
        if value.get("run_id") != run_id:
            return
        counters.inc("printer")
        print(f"[回复打印] {record.key} #{value['seq']}（来自回复分区 {record.partition}）：{value['reply']}")

    threads: List[threading.Thread] = []
    for worker_no in (1, 2):
        label = f"reply-worker-{worker_no}"
        t = threading.Thread(
            target=consume_loop,
            kwargs=dict(
                bootstrap=bootstrap, topic=TOPIC_IN, group="week14-reply-workers",
                label=label, stop=stop, ready=ready[f"reply-{worker_no}"],
                handler=make_reply_handler(worker_no),
            ),
            daemon=True,
        )
        t.start()
        threads.append(t)

    threads.append(threading.Thread(
        target=consume_loop,
        kwargs=dict(
            bootstrap=bootstrap, topic=TOPIC_IN, group="week14-audit",
            label="audit", stop=stop, ready=ready["audit"], handler=handle_audit,
        ),
        daemon=True,
    ))
    threads[-1].start()

    threads.append(threading.Thread(
        target=consume_loop,
        kwargs=dict(
            bootstrap=bootstrap, topic=TOPIC_OUT, group="week14-reply-printers",
            label="reply-printer", stop=stop, ready=ready["printer"], handler=handle_reply,
        ),
        daemon=True,
    ))
    threads[-1].start()

    # 等所有消费者加入消费组并分到分区，再开始发消息，不然会读到空
    deadline = time.time() + 20
    while time.time() < deadline and not all(ev.is_set() for ev in ready.values()):
        time.sleep(0.2)
    missing = [name for name, ev in ready.items() if not ev.is_set()]
    if missing:
        print(f"[!] 这些消费者 20 秒内没完成分区分配：{missing}，继续演示但结果可能不完整")

    # 生产消息：key=session_id，所以同一会话固定进同一个分区
    print("\n--- 生产者开始发送（key=session_id，同一会话锁定同一分区）---")
    sent: Dict[int, int] = {}
    key_partition: Dict[str, int] = {}
    try:
        for session_id in SESSIONS:
            for seq, content in enumerate(UTTERANCES, start=1):
                message = {
                    "run_id": run_id,
                    "session_id": session_id,
                    "seq": seq,
                    "role": "user",
                    "content": content,
                    "ts": time.time(),
                }
                meta = producer.send(TOPIC_IN, key=session_id, value=message).get(timeout=10)
                sent[meta.partition] = sent.get(meta.partition, 0) + 1
                key_partition[session_id] = meta.partition
                print(f"[生产] {session_id} #{seq} → 分区 {meta.partition}，offset {meta.offset}")
                time.sleep(0.05)
        producer.flush()
    except KafkaError as exc:
        print(f"[!] 发送失败：{exc}")
        stop.set()
        for t in threads:
            t.join(timeout=3)
        producer.close()
        return 1

    # 等所有消息被三个组都处理完
    print("\n--- 等待消费完成 ---")
    expected = {
        "reply_total": total,
        "audit": total,
        "printer": total,
    }
    deadline = time.time() + 60
    while time.time() < deadline:
        if all(counters.get(k) >= v for k, v in expected.items()):
            break
        time.sleep(0.3)
    else:
        print("[!] 60 秒内没消费完，下面是截止当前的结果（检查 broker / 消费者日志）")

    stop.set()
    for t in threads:
        t.join(timeout=5)
    producer.close(timeout=5)

    # ----------------------------- 结果汇总 -----------------------------
    print("\n" + "=" * 20 + " 结果汇总 " + "=" * 20)
    print(f"生产消息：{total} 条，按分区分布 {dict(sorted(sent.items()))}")
    print(f"key → 分区：{key_partition}   ← 同一个 key 始终落在同一个分区")
    print(f"会话 → 处理它的消费者：{partition_owner}  ← 组内两个消费者按分区瓜分消息，互不重复")

    snapshot = counters.snapshot()
    print("\n各消费组处理条数：")
    print(f"  reply-workers（组内 2 个消费者）：worker1={snapshot.get('reply-worker-1', 0)}, "
          f"worker2={snapshot.get('reply-worker-2', 0)}，合计 {snapshot.get('reply_total', 0)}")
    print(f"  audit（独立消费组）：{snapshot.get('audit', 0)} 条  ← 同一批消息被第二个组又消费了一遍")
    print(f"  reply-printers（消费 replies topic）：{snapshot.get('printer', 0)} 条")

    order = seen.snapshot()
    ok = all(seqs == sorted(seqs) and seqs == list(range(1, len(UTTERANCES) + 1)) for seqs in order.values())
    print(f"\n每个会话的消费顺序：{order}")
    print(f"分区内有序性检查：{'通过 ✅（每个会话都是 1→2→3）' if ok else '异常 ❌'}")

    print("\n回复示例：")
    for reply in replies[:3]:
        print(f"  {reply['session_id']} #{reply['seq']} → {reply['reply']}")

    print("\n提示：再跑一次，消费组会从上次提交的 offset 继续，不会重复处理旧消息。")
    print("      想从头再来：kafka-consumer-groups.sh --bootstrap-server localhost:9092 "
          "--delete --group week14-reply-workers（其他组同理）")
    return 0 if ok and all(counters.get(k) >= v for k, v in expected.items()) else 1


def run_produce(bootstrap: str, count: int) -> int:
    run_id = uuid.uuid4().hex[:6]
    admin = connect_admin(bootstrap)
    ensure_topics(admin, [TOPIC_IN])
    admin.close()

    producer = make_producer(bootstrap)
    print(f"run_id={run_id}，发送 {count} 条消息到 {TOPIC_IN}")
    try:
        for i in range(count):
            session_id = SESSIONS[i % len(SESSIONS)]
            seq = i // len(SESSIONS) + 1
            content = UTTERANCES[(seq - 1) % len(UTTERANCES)]
            message = {
                "run_id": run_id,
                "session_id": session_id,
                "seq": seq,
                "role": "user",
                "content": content,
                "ts": time.time(),
            }
            meta = producer.send(TOPIC_IN, key=session_id, value=message).get(timeout=10)
            print(f"  {session_id} #{seq}「{content}」→ 分区 {meta.partition}，offset {meta.offset}")
        producer.flush()
    except KafkaError as exc:
        print(f"[!] 发送失败：{exc}")
        return 1
    finally:
        producer.close(timeout=5)
    return 0


def run_consume(bootstrap: str, topic: str, group: str) -> int:
    """单独起一个观察者消费者：Ctrl-C 退出。"""
    consumer = make_consumer(bootstrap, topic, group, auto_commit=True)
    consumer.subscribe([topic])
    print(f"订阅 {topic}（消费组 {group}），Ctrl-C 退出…\n")
    try:
        while True:
            batch = consumer.poll(timeout_ms=500)
            if not batch:
                continue
            for tp, records in batch.items():
                for record in records:
                    print(
                        f"[{tp.topic} p{tp.partition} offset={record.offset}] "
                        f"key={record.key} {json.dumps(record.value, ensure_ascii=False)}"
                    )
    except KeyboardInterrupt:
        print("\n收到 Ctrl-C，退出。")
    except KafkaError as exc:
        print(f"[!] 消费出错：{exc}")
        return 1
    finally:
        consumer.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用 Kafka 传递聊天消息（练习）")
    parser.add_argument("--bootstrap", default=DEFAULT_BOOTSTRAP,
                        help=f"Kafka 地址，默认 {DEFAULT_BOOTSTRAP}")
    sub = parser.add_subparsers(dest="mode")

    sub.add_parser("demo", help="端到端演示：生产者 + 两个消费组 + 回复链路（默认）")
    p_produce = sub.add_parser("produce", help="只发送消息")
    p_produce.add_argument("--count", type=int, default=9, help="发送条数，默认 9")
    p_consume = sub.add_parser("consume", help="只消费消息，Ctrl-C 退出")
    p_consume.add_argument("--topic", default=TOPIC_IN, help=f"topic，默认 {TOPIC_IN}")
    p_consume.add_argument("--group", default="week14-observer", help="消费组，默认 week14-observer")

    args = parser.parse_args()
    mode = args.mode or "demo"

    try:
        if mode == "produce":
            return run_produce(args.bootstrap, args.count)
        if mode == "consume":
            return run_consume(args.bootstrap, args.topic, args.group)
        return run_demo(args.bootstrap)
    except KeyboardInterrupt:
        print("\n已中断。")
        return 130
    except KafkaError as exc:
        print(f"[!] Kafka 连接/操作失败：{exc}")
        print("    确认 broker 已启动，例如：")
        print("      docker compose up -d          # 本目录的 docker-compose.yml")
        print("      docker ps | grep kafka")
        print("    默认地址 localhost:9092，可用 --bootstrap 修改。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
