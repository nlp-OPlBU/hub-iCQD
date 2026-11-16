# 作业1：Company ID、Redis 与 Kafka 实践

学生：王鑫炜。公司 ID 文件为 [作业1companyID.txt](作业1companyID.txt)，文件只含 `69159812`。

两份独立程序为 `redis_demo.py`、`kafka_demo.py`。账号证明见 [Company ID 截图与记录](docs/Company-ID.md)，运行证明见 [调试截图](作业1-Redis与Kafka调试截图.jpg) 和 [验收记录](docs/验收记录.md)。

## 运行

在本目录执行。`compose.yaml` 只启动 Redis/Kafka，`Dockerfile` 只包含这两份实践程序。


```bash
docker compose up -d --wait redis kafka
source ../.venv/bin/activate  # 本机已安装的共享环境
python redis_demo.py
python redis_demo.py  # 同一个 key 的历史继续存在，不是新的内存列表
python kafka_demo.py  # 生产、消费并核对提交的 next offset

docker build -f Dockerfile -t wxw-week14-practice:local .
docker run --rm --network wxw-week14_default \
  -e REDIS_URL=redis://redis:6379/0 \
  wxw-week14-practice:local python redis_demo.py
docker run --rm --network wxw-week14_default \
  -e KAFKA_BOOTSTRAP_SERVERS=kafka:29092 \
  wxw-week14-practice:local python kafka_demo.py
```

Redis 程序真实执行 `RPUSH → LTRIM → EXPIRE → LRANGE`，重复运行可看到历史增长。Kafka 程序使用真实 broker，核对生产回调、消费值及手动提交的 `offset + 1`。两个程序已复制进 `Dockerfile` 生成的镜像；使用记录放在 `docs/`，并附实际运行截图。


复制到其他机器时，可先使用 `python3 -m venv .venv` 和 `.venv/bin/python -m pip install -r requirements.txt` 创建独立环境，再使用本目录的 `.venv/bin/activate`。

本次使用本机 Docker 完成真实运行。若在已有优云镜像内实践，将本目录复制到实例中，安装 Python/Docker 后执行同样的命令。未购买或启动付费云实例。

`docs/镜像实践记录.html` 是已保存真实 stdout 的展示页面；其中的 19 项测试结果保留拆分前联合工程的历史记录，完整测试源码位于作业2。
