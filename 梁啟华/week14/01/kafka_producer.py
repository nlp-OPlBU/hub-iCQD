#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
程序二（生产者）：把对话消息发送到 Kafka

业务场景：
    聊天服务收到用户消息后，不直接在请求链路里写数据库，而是先发到 Kafka，
    由下游消费者异步落库 / 做分析。这样接口响应快，下游也能按自己的节奏扩容。

本示例要说明的三个概念：
  * topic（主题）：消息的分类，这里是 config.yaml 中的 kafka.topic；
  * key（分区键）：这里用 session_id。Kafka 用 key 的哈希选择分区，
    因此同一个会话的消息总会进入同一个分区，分区内严格有序，
    消费者就能按顺序还原同一会话的对话；
  * offset（偏移量）：消息在分区内的唯一序号，由 broker 分配，
    发送成功后可以从返回的 metadata 里看到，消费者会用它记录“读到哪了”。

运行：
    python kafka_producer.py --demo        # 自动发送一组示例消息（含三个会话）
    python kafka_producer.py               # 交互输入，回车发送
    python kafka_producer.py --session s1  # 指定默认会话 id
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from kafka import KafkaProducer
from kafka.errors import KafkaError, KafkaTimeoutError, NoBrokersAvailable

from config_loader import kafka_api_version, load_config


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _bootstrap_list(kafka_config: dict) -> list[str]:
    """把 'a:9092,b:9092' 拆成列表，顺便去掉多余空格。"""
    raw = str(kafka_config["bootstrap_servers"])
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_producer(kafka_config: dict) -> KafkaProducer:
    """
    创建生产者。所有连接/超时参数都来自配置文件，代码只负责读取。
    """
    api_version = kafka_api_version(kafka_config)
    return KafkaProducer(
        bootstrap_servers=_bootstrap_list(kafka_config),
        # 配置了 api_version 就固定协议版本，避免客户端启动时的版本探测超时
        api_version=api_version,
        # acks=all：所有 ISR 副本确认后才算发送成功，教学环境单副本等价于写完即成功
        acks=kafka_config.get("acks", "all"),
        request_timeout_ms=int(kafka_config.get("request_timeout_ms", 5000)),
        retries=int(kafka_config.get("retries", 3)),
        # key/value 序列化器：Kafka 只认字节，发送前统一编码成 UTF-8 JSON
        key_serializer=lambda key: key.encode("utf-8"),
        value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
    )


def send_message(
    producer: KafkaProducer,
    kafka_config: dict,
    session_id: str,
    role: str,
    content: str,
) -> dict:
    """发送一条消息，并打印 broker 返回的分区与 offset。"""
    payload = {
        "session_id": session_id,
        "role": role,
        "content": content,
        "timestamp": _now_iso(),
    }
    timeout_seconds = int(kafka_config.get("request_timeout_ms", 5000)) / 1000

    # key=session_id：同一会话固定落到同一分区，从而保证该会话内的消息顺序
    future = producer.send(kafka_config["topic"], key=session_id, value=payload)
    # send() 是异步的，get() 等待 broker 确认，才能拿到 topic / partition / offset
    metadata = future.get(timeout=timeout_seconds)

    print(
        f"  已发送 -> topic={metadata.topic} "
        f"partition={metadata.partition} offset={metadata.offset} key={session_id}"
    )
    print(f"           {role}: {content}")
    return payload


def run_demo(producer: KafkaProducer, kafka_config: dict) -> None:
    """
    自动演示：交叉发送三个会话的消息。
    观察输出中的 partition：同一 session_id 每次都在同一个分区，
    这正是“按 key 分区保证同一会话顺序”的直接证据。
    """
    print("[演示] 交叉发送三个会话的消息（注意同一 session 的 partition 始终相同）：")
    dialogs = {
        "session-a": ["你好，我想订一张去上海的机票", "明天上午出发的还有吗？"],
        "session-c": ["帮我查一下订单 A123 的物流", "大概什么时候能到？"],
        "session-1": ["今天北京的天气怎么样？", "需要带伞吗？"],
    }
    for round_index in range(2):
        for session_id, messages in dialogs.items():
            content = messages[round_index]
            send_message(producer, kafka_config, session_id, "user", content)

    # flush 会阻塞到所有缓冲消息都真正发出，程序退出前建议调用一次
    producer.flush()
    print("\n[提示] 消息已全部发出；接下来运行 kafka_consumer.py 查看消费结果。")


def run_interactive(producer: KafkaProducer, kafka_config: dict, session_id: str, role: str) -> None:
    print(f"已连接 Kafka：{kafka_config['bootstrap_servers']}，topic={kafka_config['topic']}")
    print("直接输入文本发送；/switch 会话id 切换会话，/role 角色 切换角色，/quit 退出。")

    while True:
        try:
            line = input(f"\n[{session_id}] {role}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not line:
            continue
        if line.startswith("/"):
            command, _, argument = line[1:].partition(" ")
            command = command.lower()
            argument = argument.strip()
            if command in {"quit", "exit"}:
                print("已退出。")
                break
            if command == "switch" and argument:
                session_id = argument
                print(f"已切换到会话：{session_id}")
            elif command == "role" and argument in {"user", "assistant", "system"}:
                role = argument
                print(f"当前角色：{role}")
            else:
                print("可用命令：/switch 会话id、/role user|assistant|system、/quit")
            continue

        try:
            send_message(producer, kafka_config, session_id, role, line)
        except KafkaTimeoutError:
            print("  [错误] 发送超时：Kafka 可能未启动或网络不通，请检查 bootstrap_servers。")

    producer.flush()
    producer.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka 生产者：发送对话消息（教学示例）")
    parser.add_argument("--config", help="配置文件路径，默认使用脚本同目录的 config.yaml")
    parser.add_argument("--session", default="session-a", help="交互模式下的默认会话 id")
    parser.add_argument("--role", default="user", choices=["user", "assistant", "system"])
    parser.add_argument("--demo", action="store_true", help="自动发送示例消息")
    args = parser.parse_args()

    config = load_config(args.config)
    kafka_config = config["kafka"]

    try:
        producer = build_producer(kafka_config)
    except NoBrokersAvailable:
        print("[错误] 连接不到 Kafka broker，请先确认服务已启动。")
        print(f"       当前配置：{kafka_config['bootstrap_servers']}")
        print("       排查步骤：")
        print("         1) 在本目录执行：docker compose up -d kafka")
        print("         2) 执行 docker compose logs -f kafka，等待出现 Kafka Server started")
        print("         3) 检查 docker-compose.yml 的 KAFKA_ADVERTISED_LISTENERS 是否为 127.0.0.1:9092")
        sys.exit(1)

    try:
        if args.demo:
            run_demo(producer, kafka_config)
        else:
            run_interactive(producer, kafka_config, args.session, args.role)
    except KafkaError as exc:
        print(f"[错误] Kafka 操作失败：{exc}")
        sys.exit(1)
    finally:
        producer.close()


if __name__ == "__main__":
    main()
