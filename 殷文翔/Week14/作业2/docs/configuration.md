# 统一配置与日志

实现位置 `backend/app/core/config.py`、`logging.py`。API、Alembic 与后续 Worker 统一复用 Settings，不在业务模块内读取零散环境变量或硬编码连接信息。

## 加载规则

配置优先级为显式 Settings 参数 > 进程环境变量 > **项目根目录 .env** > 代码默认值。应用不自动加载 `.env.example`，它只供复制/审阅；不从当前工作目录猜测 `.env`。get_settings 缓存一个进程的配置，修改后重启项目进程。

DATA_DIR、LOG_DIR、MinerU 工作目录/配置/模型配置目录及 ES_CA_CERTS 等相对路径均基于项目根目录。创建 Settings 不创建目录、不访问网络，也不操作环境文档中的 `/workspace` 路径；Linux 服务器的路径配置不应直接照搬为 Windows 运行路径。MINERU_COMMAND 可以是 PATH 中的可执行命令或部署环境绝对路径，未来 subprocess 调用不经 shell。

敏感字段使用 SecretStr；MySQL URL 使用 SQLAlchemy URL.create 构造，密码含 `@ / %` 也不会破坏连接串。CORS_ORIGINS 为 JSON 数组，默认 `[]`，前端走同源/Vite 代理。未知变量忽略以允许同一 `.env` 存 VITE_*；拼写应对照示例核查。浏览器只接收 VITE_*，禁止将密码放入这个前缀。

## 限制与默认值

| 配置 | 默认值/含义 |
| --- | --- |
| UPLOAD_MAX_FILES | 10 个 PDF/批 |
| UPLOAD_MAX_FILE_BYTES | 52428800（50 MiB） |
| UPLOAD_MAX_REQUEST_BYTES | 209715200（200 MiB，含 multipart 开销） |
| ZIP_MAX_ENTRIES | 10000 个归档项 |
| ZIP_MAX_ENTRY_BYTES | 104857600（单项 100 MiB） |
| ZIP_MAX_UNCOMPRESSED_BYTES | 1073741824（累计 1 GiB） |
| ZIP_MAX_COMPRESSION_RATIO | 100，逐项及总体压缩比上限；零压缩大小且非空须拒绝 |
| TASK_MAX_RETRIES | 3，不含首次，最多 4 次实际执行；新任务持久化预算快照 |
| TASK_RETRY_BASE_SECONDS / MAX_SECONDS | 指数退避 5～300 秒 |
| TASK_LEASE_SECONDS / HEARTBEAT_SECONDS | 120 / 30 秒；租约必须大于两倍心跳 |
| OUTBOX_MAX_RETRIES / LEASE_SECONDS | 10 / 60 秒；投递器租约与解析租约独立 |
| OUTBOX_POLL_SECONDS / BATCH_SIZE | 1 秒 / 20 条；投递器和崩溃恢复轮询节奏 |
| KAFKA_PUBLISH_TIMEOUT_SECONDS | 10 秒；等待 broker 发送确认的上限 |
| KAFKA_CONSUMER_POLL_SECONDS | 1 秒；Worker 单次 poll 等待 |
| KAFKA_MAX_POLL_INTERVAL_MS | 3600000；需结合处理隔离方案服务器验证 |
| MINERU_TIMEOUT_SECONDS / MAX_CONCURRENCY | 1800 秒 / 1 |
| IMAGE_MAX_UPLOAD_FILES / MAX_FILE_BYTES | 4 张 / 每张 10 MiB |
| IMAGE_MAX_PIXELS / MAX_EDGE | 原图 20000000 像素 / 模型输入最大边 2048 |
| IMAGE_MAX_CONTEXT_COUNT / MAX_CONTEXT_BYTES | 用户和检索图合计 8 张 / 模型输入图合计 20 MiB |
| IMAGE_TOKEN_BUDGET | 4096，实际消耗依模型图片处理器估算 |
| RAG_CONTEXT_TOKEN_BUDGET / LLM_MAX_OUTPUT_TOKENS | 输入 12000 / 输出 2048；实际总窗口需要服务器模型验证 |
| MYSQL_CONNECT_TIMEOUT_SECONDS | 连接、读取、写入超时各 3 秒 |
| MYSQL_POOL_SIZE / POOL_RECYCLE_SECONDS | 5 / 1800 秒，开启 pool_pre_ping |
| REDIS_CACHE_TTL_SECONDS | 60 秒；不影响 MySQL 状态 |
| LLM_TIMEOUT_SECONDS | 120 秒 |

Settings 已校验类型、正整数/非负重试、端口范围及关键大小/租约/预算关系。**UPLOAD_MAX_FILES、UPLOAD_MAX_FILE_BYTES、UPLOAD_MAX_REQUEST_BYTES 已在批量上传中执行**：总请求按流式实际读取字节计数（包含 multipart 开销），请求检查通过后才开始逐文件落盘/事务。ZIP 解压、投递重试、任务租约/心跳和重试预算已在 outbox 投递器与 Worker 中执行；真实 MinerU、ES 和模型限制仍需服务器联调验证。

未来 ZIP 解压除上述限制还必须检查实际展开流量、规范化路径是否逃逸任务目录、拒绝绝对路径/`..`/链接等危险条目；不能只检查 ZIP 中声明的大小。数据目录由存储服务按需创建，上传原文件位于 `uploads/<UUID>.pdf`、请求体临时文件位于 `tmp/`、持久补偿记录位于 `tmp/upload-recovery/<UUID>.json`，均相对于 DATA_DIR。不要按文件年龄清空补偿记录；恢复命令见 [database.md](database.md#上传文件补偿)。multipart 解析的溢出暂存文件使用系统临时目录并在请求退出时关闭，部署需为项目及系统临时目录预留磁盘空间。API 与 Worker 使用同一可访问根路径和版本化 key。

MySQL 引擎显式使用 REPEATABLE READ，使一次列表查询的 total 和分页内容处于同一数据库快照。运行依赖新增 [python-multipart](https://pypi.org/project/python-multipart/)，固定 0.0.32；完整依赖记录见 backend/requirements-lock.txt。

BM25 为默认模式，重排和视觉能力默认关闭。hybrid 需要独立 embedding 模型配置，模型名不空和维度为正只是配置校验，不能替代模型与索引实测。

## 日志

标准 logging 统一输出每行 JSON 到 stderr，包含 UTC 时间、level、logger、request_id、message；LOG_LEVEL 控制级别，LOG_DIR 是保留的运行目录配置，当前不自动写日志文件，可由进程托管工具收集和轮转。

访问日志只记录匹配后的路由模板、方法、HTTP 状态和耗时，不记录完整路径/查询字符串、请求体、上传文件内容。未知路径显示 unmatched。日志过滤已配置密钥、URL 内嵌密码、Bearer 与常见 token/password 字段；异常只输出类型，响应只含通用错误，不输出原始 SQL 和堆栈中的输入。SQLAlchemy 隐藏 SQL 参数；HTTPX/HTTPCore 请求级日志默认关闭。该过滤是兜底，后续业务代码仍禁止主动日志输出敏感内容。

`.env`、运行数据、日志和构建产物由 .gitignore 排除，不能提交真实密码和原始文件。
