"""作业1-B：Kafka 生产-消费管道演示。

验证四件事：
1. 生产者 acks=all，消息落盘后才返回，拿到 partition/offset；
2. 消费者手动提交 offset（enable_auto_commit=False），失败不提交，语义是 at-least-once；
3. 处理失败按退避重试，超过上限投递到 DLQ 后再提交，避免卡死分区；
4. 用消费位点（position / committed / lag）证明提交真的生效。

用法（在 week14 根目录）：
    PYTHONPATH=backend python demo/kafka_pipe.py --count 6 --fail-at 3
"""

from __future__ import annotations

import argparse
import logging
import time
import uuid
from typing import Any

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.infra.mq.kafka_client import close_producer, create_consumer, publish


def build_payload(batch: str, seq: int) -> dict[str, Any]:
    return {"batch": batch, "seq": seq, "doc_id": f"{batch}-{seq}", "path": f"demo/{seq}.pdf"}


def handle(payload: dict[str, Any], fail_at: int) -> None:
    """业务处理：这里用抛异常模拟解析失败。"""
    time.sleep(0.05)
    if payload["seq"] == fail_at:
        raise RuntimeError(f"模拟解析失败：doc_id={payload['doc_id']}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Kafka 生产-消费管道演示")
    parser.add_argument("--count", type=int, default=6, help="投递消息数，默认 6")
    parser.add_argument("--fail-at", type=int, default=3, help="第几条处理失败并进入重试，0 表示不失败")
    parser.add_argument("--max-retries", type=int, default=2, help="同一消息最大重试次数，默认 2")
    parser.add_argument("--timeout", type=int, default=40, help="消费超时秒数，默认 40")
    args = parser.parse_args()

    setup_logging()
    # 客户端内部 INFO 日志太吵，演示只看业务输出
    logging.getLogger("kafka").setLevel(logging.WARNING)
    cfg = get_settings().kafka
    batch = uuid.uuid4().hex[:8]
    group = f"demo-pipe-{batch}"  # 独立消费组，保证从 earliest 开始读

    print(f"\n=== 1. 生产 {args.count} 条消息（topic={cfg.topic}）===")
    for seq in range(1, args.count + 1):
        publish(cfg.topic, key=f"{batch}-{seq}", payload=build_payload(batch, seq))
        print(f"  produced seq={seq} key={batch}-{seq}")

    print(f"\n=== 2. 消费（group={group} max_poll_records={cfg.max_poll_records} 手动提交）===")
    consumer = create_consumer(topics=[cfg.topic], group_id=group)
    attempts: dict[int, int] = {}
    processed: set[int] = set()
    retries = 0
    dlq = 0
    deadline = time.time() + args.timeout

    try:
        while len(processed) < args.count and time.time() < deadline:
            records = consumer.poll(timeout_ms=1000)
            if not records:
                continue
            seeked = False
            for tp, items in records.items():
                for record in items:
                    if seeked:
                        break
                    payload = record.value
                    # 只处理本批消息，历史遗留消息直接提交跳过
                    if payload.get("batch") != batch:
                        consumer.commit()
                        continue
                    seq = payload["seq"]
                    if seq in processed:
                        consumer.commit()
                        continue
                    try:
                        handle(payload, args.fail_at)
                    except RuntimeError as exc:
                        attempts[seq] = attempts.get(seq, 0) + 1
                        if attempts[seq] <= args.max_retries:
                            retries += 1
                            backoff = 0.2 * attempts[seq]
                            print(f"  seq={seq} 失败（第 {attempts[seq]} 次），{backoff:.1f}s 后重投：{exc}")
                            # 关键：不提交 offset，并把位点拨回当前消息，否则这条会被跳过
                            consumer.seek(tp, record.offset)
                            time.sleep(backoff)
                            seeked = True
                            break
                        print(f"  seq={seq} 超过重试上限，转入 {cfg.dlq_topic}")
                        publish(cfg.dlq_topic, key=payload["doc_id"], payload={**payload, "error": str(exc)})
                        dlq += 1
                    else:
                        print(f"  seq={seq} 处理成功 partition={record.partition} offset={record.offset}")

                    processed.add(seq)
                    consumer.commit()  # 只有处理完（成功或已转 DLQ）才提交

        print("\n=== 3. 消费位点 ===")
        for tp in consumer.assignment():
            committed = consumer.committed(tp)
            position = consumer.position(tp)
            lag = (position or 0) - (committed or 0)
            print(f"  {tp.topic}[{tp.partition}] committed={committed} position={position} lag={lag}")

        print("\n=== 4. 统计 ===")
        print(f"  本批消息={args.count} 已处理={len(processed)} 重试={retries} 进DLQ={dlq}")
        if processed != set(range(1, args.count + 1)):
            print(f"  警告：未处理完 {sorted(set(range(1, args.count + 1)) - processed)}")
    finally:
        consumer.close()
        close_producer()

    print("\n[kafka_pipe] 演示完成\n")


if __name__ == "__main__":
    main()
