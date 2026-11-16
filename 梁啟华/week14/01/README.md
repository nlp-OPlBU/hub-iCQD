# week14/01：Redis 与 Kafka 教学示例

两个可以独立运行的小程序，用来练习 Redis 和 Kafka 的基本用法：

| 程序 | 文件 | 说明 |
| --- | --- | --- |
| 程序一 | `redis_chat_history.py` | 用 Redis 保存多轮对话历史，支持追加、读回、只保留最近 N 条、设置过期时间 |
| 程序二 | `kafka_producer.py` / `kafka_consumer.py` | 生产者发送对话消息，消费者持续消费；用 session_id 做分区键保证同一会话有序 |

所有连接信息都放在 `config.yaml`，代码里只保留读取配置的逻辑；依赖和本地中间件由
`requirements.txt`、`docker-compose.yml` 提供。

---

## 1. 目录结构

```
01/
├── README.md                 # 本文件
├── requirements.txt          # Python 依赖
├── config.example.yaml       # 示例配置（可照抄）
├── config.yaml               # 本地实际使用的配置（改这里）
├── config_loader.py          # 统一读取 YAML + 环境变量覆盖
├── docker-compose.yml        # 本地 Redis + Kafka（KRaft 单节点）
├── redis_chat_history.py     # 程序一：Redis 对话历史 + 交互 CLI
├── kafka_producer.py         # 程序二：生产者
└── kafka_consumer.py         # 程序二：消费者
```

> 依赖清单、配置、启动脚本都放在同一层，每个示例都是“一个文件看完主流程”，方便当教学材料阅读。

---

## 2. 启动本地 Redis / Kafka

### 2.1 用 Docker Compose（推荐）

```bash
cd week14/01
docker compose up -d          # 首次会拉取镜像，稍等片刻
docker compose ps             # 两个容器都应为 running
docker compose logs -f kafka  # 看到 "Kafka Server started" 即就绪
```

端口与用途：

| 服务 | 宿主机端口 | 容器端口 | 说明 |
| --- | --- | --- | --- |
| Redis | 6379 | 6379 | 无密码，仅本地学习用 |
| Kafka | 9092 | 9092 | PLAINTEXT，无认证；KRaft 单节点，无需 ZooKeeper |

停止并清理：

```bash
docker compose down           # 停止容器，保留数据卷
docker compose down -v        # 连 Redis 数据一起删除（想从零开始时用）
```

### 2.2 不用 Docker 时

- **Redis**：Windows 可安装 Memurai 或 WSL 里 `sudo apt install redis-server`；
  macOS `brew install redis && brew services start redis`；Linux `sudo apt install redis-server`。
  默认就是 `127.0.0.1:6379`。
- **Kafka**：下载 Kafka 发行包，按官方 KRaft 快速开始启动单节点即可，保证客户端能连上
  `127.0.0.1:9092`；也可以只启动 Kafka 容器：
  `docker compose up -d kafka`。

---

## 3. 安装 Python 依赖

要求 Python 3.10+。

```bash
cd week14/01
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

如果 `config.yaml` 不存在，复制一份示例配置：

```bash
copy config.example.yaml config.yaml   # Windows
cp config.example.yaml config.yaml     # macOS / Linux
```

---

## 4. 程序一：Redis 保存历史对话

### 4.1 运行

```bash
# 自动演示：写入 4 条消息、读回历史、查看元信息，再演示“只保留最近 3 条”
python redis_chat_history.py --demo

# 交互模式：输入文本即追加一条消息，能直观看到历史累加
python redis_chat_history.py --session demo
```

交互模式支持的命令：

| 命令 | 作用 |
| --- | --- |
| `/history` | 查看当前会话完整历史（按时间顺序还原） |
| `/meta` | 查看会话元信息：标题、条数、创建/更新时间、剩余 TTL |
| `/trim 3` | 只保留最近 3 条 |
| `/ttl` / `/ttl 600` | 查看 / 重设过期时间（秒） |
| `/switch 会话id` | 切换到另一个会话 |
| `/role assistant` | 切换当前角色：user / assistant / system |
| `/clear` | 删除当前会话 |
| `/demo` | 在当前会话写入一段示例对话 |
| `/quit` | 退出 |

预期输出（节选）：

```
[演示] 向会话 'demo' 写入 4 条消息……
  + user      | 帮我用 Python 写一个快速排序…  (累计 1 条)
  + assistant | 可以，核心是选一个基准值，把比它小的放左边、大的放右边，再递归处理。…  (累计 2 条)
  ...
[演示] 读回完整历史（List 从旧到新，直接还原对话顺序）：
   1. [2026-11-11T10:30:00+08:00] user      | 帮我用 Python 写一个快速排序
   2. [2026-11-11T10:30:00+08:00] assistant | 可以，核心是选一个基准值，把比它小的放左边、大的放右边，再递归处理。
   ...
[演示] 会话元信息（Hash）：
  会话 id     : demo
  标题        : 帮我用 Python 写一个快速排序
  消息条数    : 4
  剩余过期时间: 604800 秒（约 168.0 小时）
```

### 4.2 数据结构为什么这样选

每个会话使用两个 key（前缀来自 `redis.key_prefix`，默认 `chat`）：

```
chat:session:demo:messages   List  每条消息一个 JSON 字符串
chat:session:demo:meta       Hash  会话元信息
```

消息内容示例：

```json
{"role": "user", "content": "你好", "timestamp": "2026-11-11T10:30:00+08:00"}
```

- **List 存消息序列**：`RPUSH` 是原子追加，天然按写入顺序排列；
  `LRANGE 0 -1` 读出来就是一条完整时间线，逐条 `json.loads` 即可还原成有序对话。
  如果用一个 String 存整个 JSON 数组，每次追加都要“读-改-写”，并发时会互相覆盖。
- **`LTRIM -N -1`**：只保留最近 N 条，正好实现“滑动窗口”，且不需要把老消息读出来再写回。
- **Hash 存元信息**：标题、创建/更新时间、消息条数都是独立字段，可以单独 `HSET`，
  不必把整段历史重新序列化；`HGETALL` 一次取全，做会话列表很自然。
- **TTL**：每次追加都对两个 key 执行 `EXPIRE`，实现“滑动过期”——越活跃的会话保留越久，
  长期不说话就自动清理，不用写定时任务。
- **不选 ZSet**：历史回顾按写入顺序即可，List 已经天然有序；ZSet 用毫秒时间戳做 score
  还会遇到同一毫秒冲突的问题。

### 4.3 用 redis-cli 观察底层数据

```bash
docker compose exec redis redis-cli
127.0.0.1:6379> KEYS chat:session:*
127.0.0.1:6379> TYPE chat:session:demo:messages     # list
127.0.0.1:6379> LRANGE chat:session:demo:messages 0 -1
127.0.0.1:6379> HGETALL chat:session:demo:meta
127.0.0.1:6379> TTL chat:session:demo:messages
```

---

## 5. 程序二：Kafka 传递对话消息

### 5.1 运行（开两个终端）

终端 A —— 先启动消费者（会一直等待消息）：

```bash
python kafka_consumer.py
```

终端 B —— 发送消息：

```bash
# 自动发送三个会话交错的 6 条消息
python kafka_producer.py --demo

# 或交互发送
python kafka_producer.py --session session-a
```

消费者预期输出（节选，同一次 poll 会按分区成批返回）：

```
[消费] group=chat-history-writer topic=chat-messages partition=0 offset=0 key=session-c  （首次出现，记录 key -> partition）
       session=session-c role=user time=2026-11-11T10:23:19+08:00
       content=帮我查一下订单 A123 的物流
[消费] group=chat-history-writer topic=chat-messages partition=0 offset=1 key=session-c  （同一 key 仍在该分区）
       ...
[消费] group=chat-history-writer topic=chat-messages partition=2 offset=0 key=session-a  （首次出现，记录 key -> partition）
       ...
```

生产者预期输出：

```
已发送 -> topic=chat-messages partition=2 offset=0 key=session-a
已发送 -> topic=chat-messages partition=0 offset=0 key=session-c
已发送 -> topic=chat-messages partition=1 offset=0 key=session-1
已发送 -> topic=chat-messages partition=2 offset=1 key=session-a
...
```

可以看到：`session-a` 的每条消息 partition 都是 2，`session-c` 都是 0，`session-1` 都是 1，
且各自 offset 递增——这就是“同一会话顺序消费”的依据。具体分区号由 key 的哈希和
分区总数决定，换了 topic 分区数或 key 会变，但“同一 key 恒定同一分区”不变。
注意 Kafka 只保证**分区内**有序，不同分区的消息之间没有全局先后顺序。

常用变体：

```bash
# 消费 10 条后自动退出；空闲 5 秒也退出（方便脚本验证）
python kafka_consumer.py --max-messages 10 --idle-timeout 5

# 换一个消费组，从头重放消息（原组的 offset 不受影响）
python kafka_consumer.py --group debug-group

# 同组启动第二个消费者，观察分区被分摊（两个进程各消费一部分）
python kafka_consumer.py
```

### 5.2 核心概念在代码里的位置

| 概念 | 代码位置 | 说明 |
| --- | --- | --- |
| **topic** | `config.yaml` 的 `kafka.topic` | 消息分类；生产者 `send(topic, ...)`，消费者构造时订阅 |
| **key / 分区键** | `kafka_producer.py` 的 `send_message()` | `key=session_id`，Kafka 按 key 哈希选分区，同一会话固定同一分区，分区内有序 |
| **partition / offset** | 生产者 `future.get()` 返回的 metadata；消费者打印的 `record.partition` / `record.offset` | offset 是消息在分区内的递增编号，也是消费进度的“书签” |
| **消费组** | `config.yaml` 的 `kafka.group_id` | 同组分摊分区（一条消息只被组内一个实例处理）；不同组各自维护 offset，可重复消费全量 |
| **offset 提交** | `enable_auto_commit: true` + `auto_commit_interval_ms` | 消费进度按 `(group, topic, partition)` 存到 Kafka 内部的 `__consumer_offsets`，重启后从断点继续 |

### 5.3 消费者如何正常退出

- 常驻模式：按 `Ctrl+C`，代码捕获 `KeyboardInterrupt` 后在 `finally` 中调用
  `consumer.close()`——提交最后 offset、离开消费组、释放连接。
- 自动验证：`--max-messages N` 或 `--idle-timeout 秒`，消费够数量 / 空闲够久后主动退出。

---

## 6. 配置项说明

`config.yaml`（所有值都可用环境变量覆盖，见 `config_loader.py`）：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `redis.host` / `redis.port` | 127.0.0.1 / 6379 | Redis 地址与端口 |
| `redis.db` | 0 | 逻辑库编号 |
| `redis.password` | 空 | 无密码留空 |
| `redis.socket_timeout_seconds` | 3 | 连接/读写超时，避免服务没起时长时间卡住 |
| `redis.key_prefix` | chat | key 前缀 |
| `redis.max_messages` | 50 | 每个会话最多保留的消息条数 |
| `redis.ttl_seconds` | 604800 | 会话过期时间（秒），每次追加都会续期；`<=0` 表示不过期 |
| `kafka.bootstrap_servers` | 127.0.0.1:9092 | broker 地址，多个用逗号分隔 |
| `kafka.topic` | chat-messages | 对话消息主题 |
| `kafka.group_id` | chat-history-writer | 消费组 id |
| `kafka.api_version` | "3.8" | broker 主版本.次版本；固定后可跳过版本探测，留空则自动探测 |
| `kafka.acks` | all | 生产者确认级别 |
| `kafka.request_timeout_ms` | 5000 | 生产者发送超时 |
| `kafka.retries` | 3 | 发送重试次数 |
| `kafka.auto_offset_reset` | earliest | 新消费组的起始位置：earliest / latest |
| `kafka.enable_auto_commit` | true | 是否自动提交 offset |
| `kafka.auto_commit_interval_ms` | 5000 | 自动提交间隔 |
| `kafka.poll_timeout_ms` | 1000 | 单次 poll 的阻塞时长 |
| `kafka.idle_timeout_seconds` | 0 | 空闲多久自动退出；0 表示一直等待 |

环境变量覆盖示例：

```bash
# Windows PowerShell
$env:REDIS_PORT="6380"; python redis_chat_history.py --demo

# macOS / Linux
REDIS_PORT=6380 python redis_chat_history.py --demo
```

支持的环境变量：`REDIS_HOST`、`REDIS_PORT`、`REDIS_DB`、`REDIS_PASSWORD`、
`REDIS_KEY_PREFIX`、`REDIS_MAX_MESSAGES`、`REDIS_TTL_SECONDS`、
`KAFKA_BOOTSTRAP_SERVERS`、`KAFKA_TOPIC`、`KAFKA_GROUP_ID`、
`KAFKA_AUTO_OFFSET_RESET`、`KAFKA_ENABLE_AUTO_COMMIT`、`KAFKA_POLL_TIMEOUT_MS`。

---

## 7. 常见坑

1. **Kafka `advertised listener` 配错（最常见）**
   broker 返回给客户端的是 `KAFKA_ADVERTISED_LISTENERS` 里的地址，不是客户端连的地址。
   客户端在宿主机上时必须是 `PLAINTEXT://127.0.0.1:9092`；如果写成容器名 `kafka:9092`，
   客户端会拿到一个宿主机无法解析的地址，表现为“连上了又立刻断”或一直
   `KafkaTimeoutError: Failed to update metadata`。其他容器里的客户端则应使用
   `kafka:9092`（同一 compose 网络）或 `host.docker.internal:9092`（连宿主机端口）。

2. **broker 明明在跑，客户端却报 `NoBrokersAvailable`**
   kafka-python 启动时会自动发 ApiVersions 请求探测 broker 版本，个别环境（例如经
   WSL/Docker 端口转发）这个探测会超时，随后直接抛 `NoBrokersAvailable`。
   把 `config.yaml` 里的 `kafka.api_version` 写死为 broker 的主版本.次版本（如 `"3.8"`）
   即可跳过探测。版本号要与镜像一致，升级 Kafka 后记得同步修改。

3. **Kafka 还没就绪就启动生产者/消费者**
   首次启动要初始化 KRaft 元数据，`docker compose logs -f kafka` 看到
   `Kafka Server started` 后再运行示例。连不上时脚本会提示检查 `bootstrap_servers`。

4. **Redis 连接失败（Connection refused）**
   先确认 `docker compose ps` 中 `week14-redis` 是 running；再核对 `config.yaml` 的
   `redis.host/port` 是否与端口映射一致。如果本机 6379 已被其他 Redis 占用，
   改 `docker-compose.yml` 的映射（如 `6380:6379`）并同步改 `redis.port`。
   设置了密码的 Redis 需要填 `redis.password`，否则会报 `AuthenticationError`。

5. **已有消费组不会“从头重读”**
   `auto_offset_reset: earliest` 只对“还没有提交过 offset”的组生效。想重放历史消息，
   用 `--group 新组名`，或先删掉旧组的 offset（学习环境可以直接 `docker compose down -v`）。

6. **Windows 终端中文乱码**
   若输出中文异常，先执行 `chcp 65001`，或设置 `PYTHONUTF8=1` 再运行。

7. **Docker Desktop 未启动 / WSL 后端未就绪**
   `docker compose up -d` 报 `cannot connect to the Docker daemon` 时，先启动 Docker Desktop，
   等托盘图标变为 running；使用 WSL2 后端时确认 `wsl -l -v` 中发行版状态正常。

8. **数据想从零开始**
   `docker compose down -v` 会删除 Redis 数据卷；Kafka 使用临时存储，容器删除后消息也清空。

---

## 8. 验证情况

本示例在编写时已用 Docker 实际跑通（`redis:7-alpine`、`apache/kafka:3.8.0` 单节点 KRaft）：

| 验证项 | 命令 | 结果 |
| --- | --- | --- |
| Redis 演示模式 | `python redis_chat_history.py --demo` | 4 条消息写入并读回，TTL 604800 秒，裁剪后只剩最近 3 条 |
| Redis 交互模式 | 管道输入 `/history /meta /trim 1 /ttl 600 /quit` | 历史、元信息、裁剪、续期均正确 |
| Kafka 生产者 | `python kafka_producer.py --demo` | 6 条消息发送成功；session-a→partition 2、session-c→0、session-1→1 |
| Kafka 消费者 | `python kafka_consumer.py --max-messages 6 --idle-timeout 10` | 消费 6 条，分区键与 offset 打印正确，正常退出 |
| offset 断点 | 再次运行 `python kafka_consumer.py --idle-timeout 5` | 处理 0 条，说明已从提交的 offset 继续 |
| 新消费组重放 | `python kafka_consumer.py --group debug-replay --max-messages 6` | 新组从头读到同样 6 条 |

不同机器的 key 哈希结果、分区号可能不同，只要“同一 key 分区不变”即符合预期。
