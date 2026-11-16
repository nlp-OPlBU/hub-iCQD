# week14 作业1：用 Redis 存历史对话 + 用 Kafka 传递消息

两个可以独立运行的小程序，分别练习两种中间件的典型用法：

| 文件 | 作用 | 练到的点 |
| --- | --- | --- |
| `redis_chat_history.py` | 把多轮对话按会话存进 Redis，读出后拼成大模型上下文 | List / Hash / ZSet、pipeline、EXPIRE、LTRIM、SCAN |
| `kafka_chat_bus.py` | 生产者发聊天消息，两个消费组各取所需，回复再写回另一个 topic | topic / 分区 / key 分区 / 消费组 / 手动提交 offset / 解耦 |
| `docker-compose.yml` | 本机起 Redis + Kafka（KRaft 单节点） | 可选，已有服务就不用 |
| `requirements.txt` | `redis` + `kafka-python` | |

## 一、准备环境

```bash
conda activate py312
pip install -r requirements.txt
```

> `--bootstrap` 是全局参数，要写在子命令前面：`python kafka_chat_bus.py --bootstrap host:9092 demo`。

Redis 和 Kafka 任选一种方式启动：

```bash
# 方式 A：Docker（两个都起；已有 Kafka 占用 9092 时用 `docker compose up -d redis`）
docker compose up -d
docker ps | grep week14

# 方式 B：本机已装
redis-server --port 6379 --daemonize yes
# Kafka 用你已有的 broker 即可，默认地址 localhost:9092

# 自检
redis-cli ping                                   # PONG
kafka-topics.sh --bootstrap-server localhost:9092 --list
# 如果工具没装在宿主机，用容器里的：
# docker exec <kafka容器名> /opt/kafka/bin/kafka-topics.sh --bootstrap-server localhost:9092 --list
```

> 在本机验证时用的是 Redis 8.10.0、Kafka 3.8.1（KRaft），客户端 `redis 8.1.0` + `kafka-python 2.0.3`。

## 二、程序一：Redis 存历史对话

```bash
python redis_chat_history.py          # 内置演示，跑完数据留在 Redis 里
python redis_chat_history.py --keep   # 演示前不清空旧的 chat:* 数据
```

### 数据结构怎么选

| Key | 类型 | 用途 |
| --- | --- | --- |
| `chat:{session_id}:messages` | List | 每条消息一个 JSON，`RPUSH` 追加、`LRANGE -N -1` 读最近 N 条、`LTRIM` 裁剪 |
| `chat:{session_id}:meta` | Hash | 会话元信息：创建/更新时间、轮数、最后一句 |
| `chat:{session_id}:seq` | String | `INCR` 给每条消息一个自增序号 |
| `chat:sessions` | ZSet | 会话索引，score 是最后更新时间，按活跃度倒序翻页 |

每次写入用 **pipeline** 一次提交「追加消息 + 裁剪 + 刷 TTL + 更新元信息 + 更新索引」，避免半截状态；
`EXPIRE` 让闲置会话自动过期，`LTRIM` 保证单个会话不会无限增长。

### 演示输出（节选）

```
============ 2. 读取会话 demo-1001 的历史（LRANGE 最近 3 条） ============
  # 4 11-12 23:00:22 assistant 用 LTRIM 只保留最近 N 条，再给会话 key 设 EXPIRE，闲置自动清掉。
  # 5 11-12 23:00:22 user      多个用户的会话怎么区分？
  # 6 11-12 23:00:22 assistant 每个会话一个 key，比如 chat:{session_id}:messages；再用 ZSet 按更新时间给会话排序。

============ 3. 拼成 LLM messages（这就是「对话记忆」真正被用上的地方） ============
[
  {"role": "system", "content": "你是一个乐于助人的助手。"},
  {"role": "user", "content": "我想用 Redis 存聊天记录，应该选什么数据结构？"},
  {"role": "assistant", "content": "优先用 List：RPUSH 追加、LRANGE 取最近 N 条，天然按时间排序。"},
  ...
]

============ 6. 长对话裁剪：max_messages=6，写 10 条后只剩最近 6 条 ============
  List 长度 = 6（只保留最近 6 条）
  保留下来的 seq = [5, 6, 7, 8, 9, 10]  ← 序号连续增长，说明老数据是被裁掉的，不是被覆盖的
```

演示结束后可以自己翻数据：

```bash
redis-cli LRANGE chat:demo-1001:messages 0 -1
redis-cli HGETALL chat:demo-1001:meta
redis-cli ZREVRANGE chat:sessions 0 -1 WITHSCORES
redis-cli TTL chat:demo-1001:messages
```

### 一点取舍

- **List 够用**：读「最近 N 条」是 O(N)，写入 O(1)，还自带顺序，聊天历史这种场景最省事。
- **要更强的能力就换 Stream**：需要多消费者各自游标（`XGROUP`）、消息确认（`XACK`）、断点续读时用 Redis Stream 更合适；
  本练习只做「单会话历史存取」，List 足够，也更容易看清数据结构的对应关系。
- **会话索引的幽灵条目**：会话 List 过期后 ZSet 里的会话 id 还在，演示里用 `prune_session_index()` 扫描清理；
  生产上也可以用定时任务按 score 清理，而不是每次读都扫。

## 三、程序二：Kafka 传聊天消息

```bash
python kafka_chat_bus.py                 # 端到端演示（默认）
python kafka_chat_bus.py produce         # 只发消息
python kafka_chat_bus.py consume         # 只消费，Ctrl-C 退出
python kafka_chat_bus.py consume --topic week14.chat.replies --group week14-observer-replies
```

### 演示里的拓扑

```
生产者（key=session_id）
      │
      ▼
week14.chat.messages（3 分区）
      ├── 消费组 reply-workers：2 个消费者，按分区瓜分 → 生成回复 → 写 week14.chat.replies
      └── 消费组 audit：1 个消费者，同一条消息也收到一份，只做统计
                                    week14.chat.replies
                                          └── 消费组 reply-printers → 打印回复
```

对应到 Kafka 的几个核心概念：

1. **分区与顺序**：`send(topic, key=session_id, ...)`，同一个会话始终落在同一个分区；
   分区内按 offset 有序，所以消费者看到的 seq 一定是 1→2→3。不同会话可以分散到不同分区并行处理。
2. **消费组**：组内消费者分摊分区，每条消息只被一个成员处理（横向扩容）；
   组与组之间互不影响，`audit` 组能再收到一遍全部消息（发布/订阅的语义）。
3. **offset 提交**：演示里 `enable_auto_commit=False`，处理完一批手动 `commit()`；
   进度存在 Kafka 的 `__consumer_offsets` 里，进程重启后从上次位置继续 —— 再跑一次演示不会重复处理旧消息。
4. **解耦**：生产者只管发，不关心下游有几个消费者；消费者挂了，消息还在 topic 里（受保留策略约束）。

### 演示输出（节选）

```
[reply-worker-1] 分到分区 [0, 1]
[reply-worker-2] 分到分区 [2]
--- 生产者开始发送（key=session_id，同一会话锁定同一分区）---
[生产] s-1001 #1 → 分区 1，offset 0
[审计] 围观到 s-1001 #1（分区 1，offset 0）
[回复 worker1] s-1001 #1 来自分区 1 → 回复写入分区 1（offset 0）
[回复打印] s-1001 #1（来自回复分区 1）：（worker1 自动回复）你第 1 句说的是「你好，我想了解一下 Kafka 的分…」，收到。
...
==================== 结果汇总 ====================
生产消息：9 条，按分区分布 {0: 3, 1: 3, 2: 3}
key → 分区：{'s-1001': 1, 's-1002': 2, 's-1003': 0}   ← 同一个 key 始终落在同一个分区
会话 → 处理它的消费者：{'s-1001': 1, 's-1002': 2, 's-1003': 1}  ← 组内两个消费者按分区瓜分消息，互不重复

各消费组处理条数：
  reply-workers（组内 2 个消费者）：worker1=6, worker2=3，合计 9
  audit（独立消费组）：9 条  ← 同一批消息被第二个组又消费了一遍
  reply-printers（消费 replies topic）：9 条

每个会话的消费顺序：{'s-1001': [1, 2, 3], 's-1002': [1, 2, 3], 's-1003': [1, 2, 3]}
分区内有序性检查：通过 ✅（每个会话都是 1→2→3）
```

> 分区重新分配后哪个 worker 处理哪个会话会变化（例如第二次运行时 worker1 只剩 3 条），
> 但「同一会话固定同一分区、组内不重复、组间各一份」这些性质不变。

### 想从头再消费一遍

```bash
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --list
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --describe --group week14-reply-workers
kafka-consumer-groups.sh --bootstrap-server localhost:9092 --delete --group week14-reply-workers
```

注意：删组只能在该组成员全部下线后执行。演示用的 topic 是 `week14.chat.messages` / `week14.chat.replies`，
不会碰你已有的其他 topic。

## 四、常见问题

- **`ModuleNotFoundError: No module named 'redis'`**：当前解释器不对，`conda activate py312` 后再跑（本机 `redis` 和
  `kafka-python` 装在 py312 环境里）。
- **连不上 Redis**：确认 `redis-cli ping` 是 PONG，或 `docker compose up -d redis`；端口改了就用 `--url` 或 `REDIS_URL`。
- **连不上 Kafka**：确认 `localhost:9092` 可连（`docker ps | grep kafka`），换地址用 `--bootstrap` 或
  `KAFKA_BOOTSTRAP_SERVERS`；消费端一直没输出时先看 broker 日志和 topic 是否已创建。
- **两次演示数据混在一起**：Redis 演示默认每次先清空 `chat:*`；Kafka 演示通过 `run_id` 过滤每次运行的消息，
  消费组 offset 继续往下走。想彻底重来就删消费组、或换一个 `--group`。
