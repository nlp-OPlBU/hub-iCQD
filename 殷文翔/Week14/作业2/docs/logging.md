# 日志位置与查看方法

以下路径按 2026-11-13 当前服务器部署核对；相对路径均从项目根目录 `/workspace/hub-iCQD/殷文翔/Week14/作业2` 开始。日志文件由进程的输出重定向、Nginx/MySQL 等组件的配置决定，不是设置 LOG_DIR 后就会自动产生。

## 当前日志位置

使用新增的 `bash start.sh` 时，新启动服务的输出追加到 `runtime/deployment/logs/<模块>.log`（mysql、vlm、mineru-vlm、api、dispatcher、worker、nginx），可通过 `--log-dir` 指定目录；已有进程被跳过，仍写原日志。MySQL 自身错误日志及 Nginx access/error 路径保持由各自配置决定。下表记录旧启动方式的日志位置，不代表新入口会把输出合并到 processes.log。

| 模块 | 当前文件 | 内容 |
| --- | --- | --- |
| FastAPI、outbox 投递器、PDF Worker、项目监督进程 | `runtime/deployment/processes.log` | 当前合并输出；API 请求状态/耗时、Kafka 投递、任务处理和进程启动退出 |
| 图文问答 vLLM（8100） | `runtime/pipeline-model/vllm.log` | Qwen 加载、请求状态、吞吐和模型错误；该路径沿用本轮启动时的重定向位置 |
| MinerU 专用 VLM（8104） | `runtime/deployment/mineru-vlm.log` | 专用模型启动、推理服务请求与错误 |
| 项目 Nginx（18080）访问日志 | `runtime/deployment/nginx/access.log` | 请求路径、状态码、响应字节等 |
| 项目 Nginx（18080）错误日志 | `runtime/deployment/nginx/error.log` | 代理连接失败、请求缓冲和 Nginx 错误 |
| 项目 MySQL（13306） | `runtime/deployment/mysql/mysql.log` | MySQL 启动、退出和服务错误；不是 SQL 查询明细日志 |
| 项目 MySQL 首次初始化 | `runtime/deployment/mysql/initialize.log` | 仅第一次初始化实例时产生；平时看 mysql.log |
| 共享 Kafka | `/workspace/software/kafka_2.13-4.4.0/logs/server.log` | Broker 服务日志；同目录有 controller.log、state-change.log、GC 日志等 |
| 共享 Elasticsearch | `/workspace/software/elasticsearch-8.15.3/logs/elasticsearch.log` | ES 节点日志；同目录还有 gc.log、supervisor-console.log 等 |
| 前端页面 | 浏览器开发者工具 Console、Network | 浏览器运行错误、HTTP 请求与响应；生产静态前端没有单独的 frontend.log |

共享 Kafka、ES 日志包含其他项目活动，查看即可，不应按本项目需要清空、轮转或修改它们的配置。Kafka 数据目录中的分区 `.log` 是二进制消息数据段，不是用 tail 阅读的运行日志。

当前机器还存在共享 Nginx 的 `/var/log/nginx/access.log`、`error.log`；它们与本项目独立 18080 实例的日志不同。`runtime/deployment/nginx-review/` 只用于配置校验，不是当前站点的访问日志目录。

## 常用查看命令

先进入项目根目录：

```bash
cd '/workspace/hub-iCQD/殷文翔/Week14/作业2'

# 最近 100 行；持续跟踪用 tail -F，Ctrl-C 只结束查看，不停止服务。
tail -n 100 runtime/deployment/processes.log
tail -n 100 -F runtime/deployment/processes.log

# 一起跟踪两个模型和 Nginx 错误。
tail -n 50 -F runtime/pipeline-model/vllm.log \
  runtime/deployment/mineru-vlm.log runtime/deployment/nginx/error.log

# 查看完整历史，按 / 输入关键词搜索，按 q 退出。
less runtime/deployment/processes.log

# 只筛选应用错误；没有匹配时 rg 退出码 1，不代表服务故障。
rg -n '"level": "(ERROR|CRITICAL)"' runtime/deployment/processes.log

# 按模块的关键消息筛选。
rg -n '"logger": "app.access"' runtime/deployment/processes.log
rg -n 'Outbox cycle|Outbox publish failed' runtime/deployment/processes.log
rg -n 'Task event handled|Lease|MinerU|Task result persistence failed' runtime/deployment/processes.log

# 指定任务、文档或 HTTP request_id 查找已经记录的相关行。
rg -n -- '<任务ID、文档ID或request_id>' runtime/deployment/processes.log

tail -n 100 runtime/deployment/nginx/access.log
tail -n 100 runtime/deployment/mysql/mysql.log
tail -n 100 /workspace/software/kafka_2.13-4.4.0/logs/server.log
tail -n 100 /workspace/software/elasticsearch-8.15.3/logs/elasticsearch.log
```

应用日志主要为每行 JSON，字段包括 UTC `timestamp`、`level`、`logger`、`request_id`、`message`，异常会记录 `exception_type`。同一个 processes.log 也可能包含启动时的普通文本，不能假设每行都是 JSON。API 响应头 `X-Request-ID` 可与访问日志对应；后台任务通常没有 HTTP request_id，并非每条 Worker 消息都含任务 ID，需要结合任务接口和时间查询。

## PDF 解析失败时看哪里

1. 先查询 `GET /api/tasks/<task_id>`，查看 status、attempts、error、next_attempt_at。这些来自持久化 MySQL，是状态依据。
2. 在 processes.log 中查找对应时段的 Worker/Kafka/租约错误。advanced 档涉及专用 VLM 时，同时查看 mineru-vlm.log。
3. 原始 MinerU CLI stdout/stderr 在解析过程中写入：

   `DATA_DIR/tmp/tasks/<task_id>/<lease_token>/stdout.log` 和 `stderr.log`。

   当前部署 DATA_DIR 是 `runtime/deployment/data`。处理函数会在成功或失败结束时清理这次临时目录，所以**这两个文件不是持久日志**；进程崩溃后可能残留，不能依赖它们事后排障。当前实现只持久保存任务错误码/摘要/阶段等信息，没有提供原始 CLI 日志的长期归档。

问答失败同时查看 API 返回消息的 error 和 8100 的 vllm.log。浏览器提示网络错误时，在 Network 中检查 `/api/` 响应状态，再对照 Nginx access/error.log；前端 Console 不能代替 Worker 日志。

## 后续启动时怎样保留日志

单独运行 start_api.sh/start_worker.sh 等脚本时默认输出到当前终端；dev.sh 本身也不创建 processes.log。当前文件来自启动命令的重定向。下列是**停机后重启时**的写法；已有服务时不要重复启动同端口，每条长期命令在独立终端执行，每个终端都要设置 RAG_ENV_FILE：

```bash
export RAG_ENV_FILE=runtime/deployment/project.env
mkdir -p runtime/deployment/logs

# >> 追加保留已有内容；> 会截断同名文件。
bash scripts/dev.sh --nginx-config runtime/deployment/nginx/nginx.conf \
  >> runtime/deployment/processes.log 2>&1

# 若要将问答模型日志统一到 deployment，可在下次启动时选这个新位置。
bash scripts/start_vlm.sh >> runtime/deployment/logs/vllm.log 2>&1
bash scripts/start_mineru_vlm.sh >> runtime/deployment/mineru-vlm.log 2>&1
```

当前正在运行的 8100 进程仍写 `runtime/pipeline-model/vllm.log`；上面的新位置只是后续重启的选项，不会修改现有进程。历史启动曾使用 `>`，同名进程日志不保证保留每次重启前的记录。

需要分开收集应用日志时，可分别执行已有三个启动脚本并重定向：

```bash
bash scripts/start_api.sh >> runtime/deployment/logs/api.log 2>&1
bash scripts/start_dispatcher.sh >> runtime/deployment/logs/dispatcher.log 2>&1
bash scripts/start_worker.sh >> runtime/deployment/logs/worker.log 2>&1
```

这与 dev.sh 启动同一批模块，不能同时重复运行；单独启动应用时 Nginx 需要另行安排。当前没有注册 systemd，也没有实现项目日志自动轮转、保留期限或统一任务追踪。日志保存在 Git 忽略目录，不提交原始日志；分享排障片段前核对敏感信息，不能把应用脱敏规则当成第三方模型/中间件日志的脱敏保证。

## 验收日志与报告

完整异常测试按 run_id 保存独立目录 `runtime/pipeline-acceptance/<run_id>/`，其中有 api.log、dispatcher.log、各场景 worker-*.log、nginx 日志及 report.json。当前最终通过报告的位置见 [验证记录](verification.md)；这些是历史测试证据，日常运行看 deployment 下的日志。`runtime/deployment/status.json`、`advanced-report.json` 是状态快照/验收报告，不是持续追加的运行日志。
