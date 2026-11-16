#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
程序二（消费者）：持续订阅 Kafka 主题，模拟把对话消息异步落库

三个核心概念在本文件中的体现：
  * 消费组 group_id
      - 同一个组内的多个消费者实例会“分摊”主题的所有分区，一条消息只会被组内一个实例处理；
      - 不同组之间互不影响，各自维护自己的 offset（例如“落库组”和“分析组”都能读全量）。
  * offset（偏移量）
      - 每条消息在分区内有唯一递增编号，就是下面打印的 offset；
      - 消费进度以 (group_id, topic, partition) 为单位提交到 Kafka 内部的
        __consumer_offsets 主题，下次启动就能从上次位置继续。
  * 分区键 key
      - 生产者用 session_id 作为 key，Kafka 按 key 哈希选分区；
      - 所以同一会话的消息一定在同一分区，且 offset 递增，消费顺序与发送顺序一致。

退出方式：
    * 交互/常驻模式：Ctrl+C 优雅退出（关闭消费者、提交 offset、离开消费组）；
    * 自动验证时可加 --max-messages N 或 --idle-timeout 秒数 让程序自行结束。

运行：
    python kafka_consumer.py
    python kafka_consumer.py --max-messages 10 --idle-timeout 5
    python kafka_consumer.py --group debug-group      # 换一个消费组可从头重放
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from kafka import KafkaConsumer
from kafka.errors import KafkaError, NoBrokersAvailable

from config_loader import kafka_api_version, load_config


def _bootstrap_list(kafka_config: dict) -> list[str]:
    raw = str(kafka_config["bootstrap_servers"])
    return [item.strip() for item in raw.split(",") if item.strip()]


def build_consumer(kafka_config: dict, group_id: str) -> KafkaConsumer:
    """
    创建消费者。用 poll() 手动循环而不是 for message in consumer，
    这样可以在“没有新消息”时检查空闲超时，方便教学演示和自动退出。
    """
    return KafkaConsumer(
        kafka_config["topic"],
        bootstrap_servers=_bootstrap_list(kafka_config),
        # 配置了 api_version 就固定协议版本，避免版本探测超时导致 NoBrokersAvailable
        api_version=kafka_api_version(kafka_config),
        group_id=group_id,
        # 该消费组第一次消费（或 offset 已被清理）时从哪里开始：earliest=最早，latest=最新
        auto_offset_reset=kafka_config.get("auto_offset_reset", "earliest"),
        # 自动提交：每隔 auto_commit_interval_ms 把已 poll 到的最大 offset 提交上去
        enable_auto_commit=bool(kafka_config.get("enable_auto_commit", True)),
        auto_commit_interval_ms=int(kafka_config.get("auto_commit_interval_ms", 5000)),
        # 不设置 consumer_timeout_ms，避免迭代器在空闲时自动抛 StopIteration；
        # 空闲退出由下面的 idle_timeout 逻辑显式控制
    )


def handle_record(record, group_id: str, key_partitions: dict[str, int]) -> bool:
    """
    处理一条消息（这里用打印模拟“异步写入历史库”）。
    返回 False 表示消息体不是合法 JSON，已跳过。
    """
    key = record.key.decode("utf-8") if record.key else "<无 key>"

    # 记录 key -> partition 的对应关系，用于直观展示“同一 key 永远同一分区”
    if key not in key_partitions:
        key_partitions[key] = record.partition
        partition_note = "  （首次出现，记录 key -> partition）"
    elif key_partitions[key] != record.partition:
        partition_note = "  <-- 异常：同一 key 换到了不同分区"
    else:
        partition_note = "  （同一 key 仍在该分区）"

    try:
        payload = json.loads(record.value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(
            f"[跳过] partition={record.partition} offset={record.offset} "
            f"消息不是合法 JSON：{exc}"
        )
        return False

    print(
        f"[消费] group={group_id} topic={record.topic} "
        f"partition={record.partition} offset={record.offset} key={key}{partition_note}"
    )
    print(
        f"       session={payload.get('session_id', '-')} "
        f"role={payload.get('role', '-')} time={payload.get('timestamp', '-')}"
    )
    print(f"       content={payload.get('content', '')}")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka 消费者：持续消费对话消息（教学示例）")
    parser.add_argument("--config", help="配置文件路径，默认使用脚本同目录的 config.yaml")
    parser.add_argument("--group", help="覆盖配置中的消费组 id（换个组可从头重放消息）")
    parser.add_argument("--max-messages", type=int, default=0,
                        help="消费到 N 条后自动退出；0 表示一直消费")
    parser.add_argument("--idle-timeout", type=int,
                        help="连续 N 秒没有新消息就退出；不填则使用配置值（0=一直等待）")
    args = parser.parse_args()

    config = load_config(args.config)
    kafka_config = config["kafka"]
    group_id = args.group or kafka_config["group_id"]
    poll_timeout_ms = int(kafka_config.get("poll_timeout_ms", 1000))
    idle_timeout = (
        args.idle_timeout
        if args.idle_timeout is not None
        else int(kafka_config.get("idle_timeout_seconds", 0))
    )

    print("=" * 72)
    print("Kafka 消费者启动")
    print(f"  bootstrap_servers = {kafka_config['bootstrap_servers']}")
    print(f"  topic             = {kafka_config['topic']}")
    print(f"  group_id          = {group_id}（同组分摊分区，不同组各自消费全量）")
    print(f"  auto_offset_reset = {kafka_config.get('auto_offset_reset', 'earliest')}")
    print(f"  自动提交 offset   = {kafka_config.get('enable_auto_commit', True)}")
    print(f"  空闲退出          = {idle_timeout} 秒（0 表示一直等待，Ctrl+C 退出）")
    print("=" * 72)
    print("等待消息中……（Ctrl+C 可随时退出）")

    try:
        consumer = build_consumer(kafka_config, group_id)
    except NoBrokersAvailable:
        print("[错误] 连接不到 Kafka broker，请先确认服务已启动。")
        print(f"       当前配置：{kafka_config['bootstrap_servers']}")
        print("       排查：docker compose up -d kafka && docker compose logs -f kafka")
        sys.exit(1)

    processed = 0
    last_active_at = time.monotonic()
    key_partitions: dict[str, int] = {}

    try:
        while True:
            # poll() 返回 {TopicPartition: [ConsumerRecord, ...]}，可能为空
            records = consumer.poll(timeout_ms=poll_timeout_ms)

            if not records:
                if idle_timeout > 0 and time.monotonic() - last_active_at >= idle_timeout:
                    print(f"\n已连续 {idle_timeout} 秒没有新消息，自动退出。")
                    break
                continue

            for batch in records.values():
                for record in batch:
                    last_active_at = time.monotonic()
                    if handle_record(record, group_id, key_partitions):
                        processed += 1
                    if args.max_messages and processed >= args.max_messages:
                        print(f"\n已达到 --max-messages={args.max_messages}，退出。")
                        return

    except KeyboardInterrupt:
        # Ctrl+C 是常驻消费者的正常退出方式，不是错误
        print("\n收到 Ctrl+C，正在优雅退出……")
    except KafkaError as exc:
        print(f"[错误] 消费过程中出现 Kafka 异常：{exc}")
        sys.exit(1)
    finally:
        # close() 会：提交最后一次 offset（自动提交模式下）→ 离开消费组 → 释放连接
        consumer.close()
        print(f"消费者已关闭，本次共处理 {processed} 条消息；"
              f"消费组 {group_id!r} 的 offset 已记录，下次启动会从断点继续。")


if __name__ == "__main__":
    main()
