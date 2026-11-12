"""消息总线 —— 对 Kafka 的一层薄封装（生产 / 消费 / 建 topic / 健康检查）。

为什么要有这一层
----------------
1. **一个开关切换两种后端**：
   - ``USE_KAFKA=1``（默认）：真 Kafka broker，进程可以拆成 Web / 解析 / 入库 / 问答 多个，
     靠 topic 解耦 —— 这是本作业的主线；
   - Kafka 连不上（或 ``USE_KAFKA=0``）且 ``ALLOW_LOCAL_FALLBACK=1``：自动退化成
     **进程内总线**（带环形缓冲重放，语义尽量贴近 Kafka 的「按 offset 顺序消费」），
     这样只改一行配置就能在没有 broker 的机器上演示前端与状态流。
     降级状态会在 ``/api/health`` 与页面横幅上如实显示，不会假装在用 Kafka。
2. 统一 JSON 序列化、key 分区、错误处理、消费线程生命周期。

用法
----
    from bus import get_bus
    bus = get_bus()
    bus.ensure_topics()                                  # 启动时建 topic（幂等）
    sub = bus.subscribe(config.TOPIC_STATUS, on_status,  # 回调里不要做重活
                        group="mmrag-web", earliest=False)
    bus.publish(config.TOPIC_UPLOAD, payload, key=doc_id)
    sub.stop()

key 用 ``doc_id`` / ``request_id``：同一文档（同一请求）的消息进同一分区，
被同一消费者按序处理 —— 否则「解析中」可能比「待解析」先到，状态机会倒着走。
"""
from __future__ import annotations

import json
import queue
import threading
import time
import traceback
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

import config

Handlers = List[Callable[[Dict[str, Any], Optional[str]], None]]

# 本地模式每个 topic 保留多少条历史，供后注册的订阅者重放
_LOCAL_BUFFER = 512


def _bootstrap_list() -> List[str]:
    return [s.strip() for s in config.KAFKA_BOOTSTRAP_SERVERS.split(",") if s.strip()]


_LAST_PROBE_ERROR = ""          # 探测失败原因，供 /api/health 如实说明「为什么降级」


def probe_error() -> str:
    return _LAST_PROBE_ERROR


def kafka_available(timeout_ms: int = 2500) -> bool:
    """探测 broker 是否可连（只做 metadata 请求，不建 topic）。

    两个 kafka-python 2.0.x 的坑（都会让探测「看起来失败」从而悄悄降级，务必按正确签名调用）：
    * ``KafkaAdminClient.list_topics()`` **不接受 timeout 参数**；
    * ``KafkaAdminClient`` **不认识 ``connection_timeout_ms``**，传了会在构造时抛
      ``KafkaConfigurationError: Unrecognized configs`` —— 我就是这样被坑过一次。
    超时只放在 ``request_timeout_ms``。失败原因一律记到 ``_LAST_PROBE_ERROR`` 并打印，不静默吞掉。
    """
    global _LAST_PROBE_ERROR
    if not config.USE_KAFKA:
        _LAST_PROBE_ERROR = "USE_KAFKA=0（主动选择了进程内队列）"
        return False
    admin = None
    try:
        from kafka import KafkaAdminClient

        admin = KafkaAdminClient(
            bootstrap_servers=_bootstrap_list(),
            client_id=f"{config.KAFKA_CLIENT_ID}-probe",
            request_timeout_ms=timeout_ms + 2000,
        )
        admin.list_topics()
        _LAST_PROBE_ERROR = ""
        return True
    except Exception as exc:  # noqa: BLE001
        _LAST_PROBE_ERROR = f"{type(exc).__name__}: {exc}"
        print(f"[bus] Kafka 探测失败（{config.KAFKA_BOOTSTRAP_SERVERS}）：{_LAST_PROBE_ERROR}",
              flush=True)
        return False
    finally:
        if admin is not None:
            try:
                admin.close(timeout_ms=1000)
            except Exception:  # noqa: BLE001
                pass


class Subscription:
    """一次订阅的句柄；``stop()`` 停线程 /摘回调。"""

    def __init__(self, bus: "MessageBus", topic: str, handler: Callable, group: str,
                 earliest: bool, name: str):
        self.bus = bus
        self.topic = topic
        self.handler = handler
        self.group = group
        self.earliest = earliest
        self.name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.received = 0
        self.errors = 0
        self.last_error = ""

    # -- 内部：被订阅的消息分发（异常不外抛，避免拖垮消费线程）--
    def _dispatch(self, payload: Dict[str, Any], key: Optional[str]) -> None:
        try:
            self.received += 1
            self.handler(payload, key)
        except Exception as exc:  # noqa: BLE001
            self.errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.bus._note(f"订阅回调异常 topic={self.topic} name={self.name}: {exc}")
            traceback.print_exc()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        with self.bus._lock:
            self.bus._local_handlers[self.topic] = [
                h for h in self.bus._local_handlers[self.topic] if h is not self
            ]
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive()) if self._thread else True


class MessageBus:
    """Kafka 后端 + 进程内后端，对外接口一致。"""

    def __init__(self, use_kafka: Optional[bool] = None):
        self._lock = threading.RLock()
        self._producer = None
        self._consumers: List[Any] = []
        self._subscriptions: List[Subscription] = []
        self._local_handlers: Dict[str, Handlers] = defaultdict(list)
        # 本地模式：**每个 topic 一条分发线程 + 一条队列**，语义尽量贴近 Kafka
        # （异步、顺序、单消费者），这样上传接口不会被解析阻塞。
        self._local_queues: Dict[str, "queue.Queue"] = defaultdict(queue.Queue)
        self._local_threads: Dict[str, threading.Thread] = {}
        self._local_buffer: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=_LOCAL_BUFFER)
        )
        self._log: List[str] = []
        self.published = 0
        self.consume_total = 0

        if use_kafka is None:
            use_kafka = config.USE_KAFKA and kafka_available()
            if config.USE_KAFKA and not use_kafka and not config.ALLOW_LOCAL_FALLBACK:
                raise RuntimeError(
                    f"连不上 Kafka broker（{config.KAFKA_BOOTSTRAP_SERVERS}），"
                    "且 ALLOW_LOCAL_FALLBACK=0。请先 `docker compose up -d`。"
                )
        self.kafka_mode = bool(use_kafka)
        self.fallback_reason = "" if self.kafka_mode else (_LAST_PROBE_ERROR or "USE_KAFKA=0")
        self._note(
            f"消息总线后端={'Kafka ' + config.KAFKA_BOOTSTRAP_SERVERS if self.kafka_mode else '进程内队列（已降级）'}"
            + ("" if self.kafka_mode else f" —— 原因：{self.fallback_reason}")
        )

    # ------------------------------------------------------------------ 日志
    def _note(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] [bus] {msg}"
        with self._lock:
            self._log.append(line)
            self._log = self._log[-200:]
        print(line, flush=True)

    def recent_log(self, n: int = 20) -> List[str]:
        with self._lock:
            return list(self._log[-n:])

    # ------------------------------------------------------------- Kafka 懒建
    def _ensure_producer(self):
        if self._producer is not None:
            return self._producer
        from kafka import KafkaProducer

        with self._lock:
            if self._producer is None:
                self._producer = KafkaProducer(
                    bootstrap_servers=_bootstrap_list(),
                    client_id=f"{config.KAFKA_CLIENT_ID}-producer",
                    acks=1,
                    linger_ms=10,
                    retries=3,
                    request_timeout_ms=15000,
                    max_in_flight_requests_per_connection=1,
                    value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode("utf-8"),
                    key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
                )
        return self._producer

    def ensure_topics(self, topics: Optional[List[str]] = None) -> List[str]:
        """建 topic（幂等）。返回实际使用的后端说明。"""
        topics = topics or [
            config.TOPIC_UPLOAD,
            config.TOPIC_PARSED,
            config.TOPIC_STATUS,
            config.TOPIC_QA_REQ,
            config.TOPIC_QA_ANS,
        ]
        if not self.kafka_mode:
            for t in topics:
                _ = self._local_buffer[t]
            self._note(f"本地模式预置 topic: {', '.join(topics)}")
            return topics
        try:
            from kafka.admin import KafkaAdminClient, NewTopic
            from kafka.errors import TopicAlreadyExistsError

            admin = KafkaAdminClient(
                bootstrap_servers=_bootstrap_list(), client_id=f"{config.KAFKA_CLIENT_ID}-admin"
            )
            try:
                existing = set(admin.list_topics())
                need = [
                    NewTopic(name=t, num_partitions=config.KAFKA_PARTITIONS,
                             replication_factor=config.KAFKA_REPLICATION)
                    for t in topics if t not in existing
                ]
                if need:
                    try:
                        admin.create_topics(need)
                        self._note(f"已创建 topic: {[n.name for n in need]}")
                    except TopicAlreadyExistsError:
                        pass
            finally:
                admin.close()
        except Exception as exc:  # noqa: BLE001
            self._note(f"建 topic 失败（broker 可能已开启 auto.create），忽略: {exc}")
        return topics

    # ---------------------------------------------------------------- 生产
    def publish(self, topic: str, payload: Dict[str, Any], key: Optional[str] = None) -> None:
        with self._lock:
            self.published += 1
        if self.kafka_mode:
            try:
                future = self._ensure_producer().send(topic, value=payload, key=key)
                # 不阻塞调用方，但把失败打到日志（生产环境应改为回调 + 重试指标）
                future.add_errback(
                    lambda exc: self._note(f"投递失败 topic={topic} key={key}: {exc}")
                )
                return
            except Exception as exc:  # noqa: BLE001
                self._note(f"Kafka 投递异常 topic={topic}: {exc} -> 转本地分发")
                if not config.ALLOW_LOCAL_FALLBACK:
                    raise
        # 本地模式：进缓冲 + 交给该 topic 的分发线程（异步，不阻塞调用方）
        self._local_buffer[topic].append((payload, key))
        self._local_publish(topic, payload, key)

    def _local_publish(self, topic: str, payload: Dict[str, Any], key: Optional[str]) -> None:
        with self._lock:
            q = self._local_queues[topic]
            if topic not in self._local_threads or not self._local_threads[topic].is_alive():
                t = threading.Thread(target=self._local_loop, args=(topic,), daemon=True,
                                     name=f"local-{topic}"[:64])
                self._local_threads[topic] = t
                t.start()
        q.put((payload, key))

    def _local_loop(self, topic: str) -> None:
        """本地模式的分发线程：等价于 Kafka 的单分区顺序消费。"""
        q = self._local_queues[topic]
        while True:
            item = q.get()
            if item is None:            # 关闭哨兵
                break
            payload, key = item
            self._fanout_local(topic, payload, key)

    def _fanout_local(self, topic: str, payload: Dict[str, Any], key: Optional[str]) -> None:
        with self._lock:
            handlers = list(self._local_handlers[topic])
        for sub in handlers:
            sub._dispatch(payload, key)

    def flush(self, timeout: float = 5.0) -> None:
        if self.kafka_mode and self._producer is not None:
            try:
                self._producer.flush(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass

    def _fanout_local(self, topic: str, payload: Dict[str, Any], key: Optional[str],
                      seq: int = 0) -> None:
        with self._lock:
            handlers = list(self._local_handlers[topic])
        for sub in handlers:
            sub._dispatch(payload, key)

    # ---------------------------------------------------------------- 消费
    def subscribe(
        self,
        topic: str,
        handler: Callable[[Dict[str, Any], Optional[str]], None],
        group: str = "",
        earliest: bool = True,
        name: str = "",
        replay_local: bool = True,
    ) -> Subscription:
        """订阅一个 topic。

        group 为空 = 每个订阅者独立消费（Web 的 SSE 转发用这个，等价于每次新建 consumer group）；
        earliest=False 表示只收「从现在起」的新消息（状态快照请走 /api/documents，不用回放历史）。
        """
        sub = Subscription(self, topic, handler, group or f"{config.KAFKA_CLIENT_ID}-{id(handler)}",
                           earliest, name or topic)
        with self._lock:
            self._subscriptions.append(sub)
            if not self.kafka_mode:
                self._local_handlers[topic].append(sub)
        if self.kafka_mode:
            sub._thread = threading.Thread(
                target=self._kafka_loop, args=(sub,), daemon=True,
                name=f"kafka-{topic}-{sub.name}"[:64],
            )
            sub._thread.start()
        elif replay_local:
            # 本地模式模拟 earliest：把缓冲里的历史先喂给新订阅者
            with self._lock:
                backlog = list(self._local_buffer[topic])
            for payload, key in backlog:
                sub._dispatch(payload, key)
        self._note(
            f"订阅 topic={topic} group={sub.group} mode={'kafka' if self.kafka_mode else 'local'}"
            f" earliest={earliest} name={sub.name}"
        )
        return sub

    def _kafka_loop(self, sub: Subscription) -> None:
        from kafka import KafkaConsumer

        last_error_at = 0.0
        while not sub._stop.is_set():
            consumer = None
            try:
                consumer = KafkaConsumer(
                    sub.topic,
                    bootstrap_servers=_bootstrap_list(),
                    group_id=sub.group,
                    client_id=f"{config.KAFKA_CLIENT_ID}-{sub.name}"[:64],
                    enable_auto_commit=True,
                    auto_commit_interval_ms=1000,
                    auto_offset_reset="earliest" if sub.earliest else "latest",
                    # 关键：手动 poll 循环里必须自己及时提交，否则 rebalance 会卡住分区
                    max_poll_interval_ms=600000,
                    session_timeout_ms=30000,
                    request_timeout_ms=40000,
                    consumer_timeout_ms=None,
                    value_deserializer=lambda b: json.loads(b.decode("utf-8")) if b else {},
                    key_deserializer=lambda k: k.decode("utf-8") if k else None,
                )
                with self._lock:
                    self._consumers.append(consumer)
                self._note(f"消费者已就绪 topic={sub.topic} group={sub.group}")
                while not sub._stop.is_set():
                    batches = consumer.poll(timeout_ms=300, max_records=50)
                    for tp_records in batches.values():
                        for record in tp_records:
                            # kafka-python 2.0.3 的 ConsumerRecord 没有 .error 字段，
                            # 直接访问会让消费线程崩掉并无限重连（消息一条都收不到）
                            if getattr(record, "error", None) is not None:
                                sub.errors += 1
                                continue
                            self.consume_total += 1
                            sub._dispatch(record.value, record.key)
            except Exception as exc:  # noqa: BLE001
                sub.errors += 1
                sub.last_error = f"{type(exc).__name__}: {exc}"
                now = time.time()
                if now - last_error_at > 10:
                    last_error_at = now
                    self._note(f"消费异常 topic={sub.topic}: {sub.last_error}（10s 后重连）")
                sub._stop.wait(1.0)
            finally:
                if consumer is not None:
                    with self._lock:
                        self._consumers = [c for c in self._consumers if c is not consumer]
                    try:
                        consumer.close(timeout_ms=2000)
                    except Exception:  # noqa: BLE001
                        pass

    # -------------------------------------------------------------- 运维信息
    def status(self) -> Dict[str, Any]:
        info: Dict[str, Any] = {
            "backend": "kafka" if self.kafka_mode else "local",
            "fallback_reason": getattr(self, "fallback_reason", ""),
            "servers": config.KAFKA_BOOTSTRAP_SERVERS,
            "published": self.published,
            "consumed": self.consume_total,
            "subscriptions": [
                {
                    "topic": s.topic,
                    "group": s.group,
                    "name": s.name,
                    "received": s.received,
                    "errors": s.errors,
                    "last_error": s.last_error,
                    "alive": s.alive(),
                }
                for s in self._subscriptions
                if not s._stop.is_set()
            ],
        }
        if self.kafka_mode:
            try:
                from kafka import KafkaAdminClient

                admin = KafkaAdminClient(bootstrap_servers=_bootstrap_list(),
                                         client_id=f"{config.KAFKA_CLIENT_ID}-status")
                try:
                    topics = admin.list_topics()
                    info["topics"] = sorted(topics)
                    try:
                        groups = admin.list_consumer_groups()
                        info["consumer_groups"] = sorted(
                            g[0] for g in groups if isinstance(g, (list, tuple))
                        )
                    except Exception:  # noqa: BLE001
                        info["consumer_groups"] = []
                finally:
                    admin.close()
            except Exception as exc:  # noqa: BLE001
                info["topics_error"] = str(exc)
        return info

    def close(self) -> None:
        for sub in list(self._subscriptions):
            sub.stop(timeout=3)
        # 停掉本地模式的分发线程
        with self._lock:
            topics = list(self._local_threads)
        for t in topics:
            try:
                self._local_queues[t].put(None)
            except Exception:  # noqa: BLE001
                pass
        for t in topics:
            thread = self._local_threads.get(t)
            if thread and thread.is_alive():
                thread.join(timeout=2)
        if self._producer is not None:
            self.flush()
            try:
                self._producer.close(timeout=5)
            except Exception:  # noqa: BLE001
                pass


_bus: Optional[MessageBus] = None
_bus_lock = threading.Lock()


def get_bus() -> MessageBus:
    """进程内单例。"""
    global _bus
    with _bus_lock:
        if _bus is None:
            _bus = MessageBus()
    return _bus


def reset_bus() -> None:
    """测试用：丢掉单例，下次 get_bus() 重新探测后端。"""
    global _bus
    with _bus_lock:
        if _bus is not None:
            _bus.close()
        _bus = None


if __name__ == "__main__":  # python bus.py -> 打印后端与 topic 状态，并做一次收发往返自检
    import pprint

    bus = get_bus()
    bus.ensure_topics()

    got: List[Dict[str, Any]] = []
    done = threading.Event()

    def _on_status(payload: Dict[str, Any], key: Optional[str]) -> None:
        got.append(payload)
        if payload.get("doc_id") == "selfcheck":
            done.set()

    # 先订阅再发布：本地模式和 Kafka 模式下都能收到（Kafka 靠 group offset 从 earliest 起算）
    sub = bus.subscribe(config.TOPIC_STATUS, _on_status, group=f"{config.KAFKA_CLIENT_ID}-selfcheck",
                        earliest=True, name="selfcheck-sub", replay_local=False)
    bus.publish(config.TOPIC_STATUS, {
        "type": "status", "doc_id": "selfcheck", "state": "done", "progress": 100,
        "stage_label": "总线自检", "message": "produce -> consume 往返正常",
    }, key="selfcheck")
    bus.flush()
    ok = done.wait(timeout=15)
    print("\n--- bus.status() ---")
    pprint.pprint(bus.status())
    print("往返自检:", "通过" if ok else "超时未收到（检查 broker / topic）", got[-1:] if got else "")
    sub.stop()
    bus.close()
    raise SystemExit(0 if ok else 1)
