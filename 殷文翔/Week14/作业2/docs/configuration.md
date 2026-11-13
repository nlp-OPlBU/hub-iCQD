# 统一配置与日志

实现位置 `backend/app/core/config.py`、`logging.py`。API、Alembic 与后续 Worker 统一复用 Settings，不在业务模块内读取零散环境变量或硬编码连接信息。

## 加载规则

配置优先级为显式 Settings 参数 > 进程环境变量 > **项目根目录 .env** > 代码默认值。运行入口可通过 `RAG_ENV_FILE=runtime/deployment/project.env` 选择单独的部署文件（替代默认 .env，不合并；指定文件不存在立即报错），相对文件名基于项目根目录。应用不自动加载 `.env.example`，它只供复制/审阅；不从当前工作目录猜测 `.env`。get_settings 缓存一个进程的配置，修改后重启项目进程。

DATA_DIR、LOG_DIR、MinerU 工作目录/配置/模型配置目录及 ES_CA_CERTS 等相对路径均基于项目根目录。创建 Settings 不创建目录、不访问网络，也不操作环境文档中的 `/workspace` 路径；Linux 服务器的路径配置不应直接照搬为 Windows 运行路径。MINERU_COMMAND 可以是 PATH 中的可执行命令或部署环境绝对路径，subprocess 调用不经 shell。

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
| KAFKA_PUBLISH_TIMEOUT_SECONDS | 10 秒；分别限制 bootstrap、等待入队、请求和 broker 确认；整个 publish 可能包含连续阶段，不是全调用总时限 |
| KAFKA_SESSION_TIMEOUT_MS / HEARTBEAT_INTERVAL_MS | 10000 / 3000；session 必须超过两倍 heartbeat，须在 broker 允许范围内；独立于 MySQL 任务租约 |
| KAFKA_CONSUMER_POLL_SECONDS | 1 秒；Worker 单次 poll 等待 |
| KAFKA_MAX_POLL_INTERVAL_MS | 3600000；需结合处理隔离方案服务器验证 |
| MINERU_TIMEOUT_SECONDS / MAX_CONCURRENCY | 1800 秒 / 1 |
| MINERU_DEVICE_MODE / OFFLINE / CPU_THREADS | 可选 cpu/cuda（未设时沿用 MinerU 自动选择）/ false / 2；Worker 向 CLI 传设备、离线和 CPU 线程环境 |
| MINERU_VLM_GPU_MEMORY_UTILIZATION / MAX_MODEL_LEN / MAX_NUM_SEQS | 0.18 / 8192 / 2；专用 VLM 启动入口使用，端口与模型名来自 MINERU_CONFIG 的 model.vlm |
| LLM_MODEL_PATH / GPU_MEMORY_UTILIZATION / MAX_NUM_SEQS | models/Qwen3.5-4B / 0.32 / 2；项目本地 VLM 启动入口使用，端口取 LLM_BASE_URL |
| IMAGE_MAX_UPLOAD_FILES / MAX_FILE_BYTES | 4 张 / 每张 10 MiB |
| IMAGE_MAX_PIXELS / MAX_EDGE | 原图 20000000 像素 / 模型输入最大边 2048 |
| IMAGE_MAX_CONTEXT_COUNT / MAX_CONTEXT_BYTES | 用户和检索图合计 8 张 / 模型输入图合计 20 MiB |
| IMAGE_TOKEN_BUDGET | 4096，实际消耗依模型图片处理器估算 |
| RAG_CONTEXT_TOKEN_BUDGET / LLM_MAX_OUTPUT_TOKENS | 输入 12000 / 输出 2048；实际总窗口需要服务器模型验证 |
| MYSQL_CONNECT_TIMEOUT_SECONDS | 连接、读取、写入超时各 3 秒 |
| MYSQL_POOL_SIZE / POOL_RECYCLE_SECONDS | 5 / 1800 秒，开启 pool_pre_ping |
| REDIS_CACHE_TTL_SECONDS | 60 秒；不影响 MySQL 状态 |
| LLM_TIMEOUT_SECONDS | 120 秒 |
| LLM_CONTEXT_WINDOW_TOKENS | 16384；输入预算 + 输出上限不得超过此值，须与实际模型窗口一致 |
| RETRIEVAL_TOP_K | 6，问答召回上限，范围 1～30 |

Settings 已校验类型、正整数/非负重试、端口范围及关键大小/租约/预算关系。**UPLOAD_MAX_FILES、UPLOAD_MAX_FILE_BYTES、UPLOAD_MAX_REQUEST_BYTES 已在批量上传中执行**：总请求按流式实际读取字节计数（包含 multipart 开销），请求检查通过后才开始逐文件落盘/事务。ZIP 解压、投递重试、任务租约/心跳和重试预算已在 outbox 投递器与 Worker 中执行；真实 MinerU、ES 和模型限制仍需服务器联调验证。

未来 ZIP 解压除上述限制还必须检查实际展开流量、规范化路径是否逃逸任务目录、拒绝绝对路径/`..`/链接等危险条目；不能只检查 ZIP 中声明的大小。数据目录由存储服务按需创建，上传原文件位于 `uploads/<UUID>.pdf`、请求体临时文件位于 `tmp/`、持久补偿记录位于 `tmp/upload-recovery/<UUID>.json`，均相对于 DATA_DIR。不要按文件年龄清空补偿记录；恢复命令见 [database.md](database.md#上传文件补偿)。multipart 解析的溢出暂存文件使用系统临时目录并在请求退出时关闭，部署需为项目及系统临时目录预留磁盘空间。API 与 Worker 使用同一可访问根路径和版本化 key。

MySQL 引擎显式使用 REPEATABLE READ，使一次列表查询的 total 和分页内容处于同一数据库快照。运行依赖新增 [python-multipart](https://pypi.org/project/python-multipart/)，固定 0.0.32；完整依赖记录见 backend/requirements-lock.txt。

BM25 为默认模式，重排和视觉能力默认关闭。hybrid 需要独立 embedding 模型配置，模型名不空和维度为正只是配置校验，不能替代模型与索引实测。

## 日志

标准 logging 统一输出每行 JSON 到 stderr，包含 UTC 时间、level、logger、request_id、message；LOG_LEVEL 控制级别，LOG_DIR 是保留的运行目录配置，当前不自动写日志文件，可由进程托管工具收集和轮转。

访问日志只记录匹配后的路由模板、方法、HTTP 状态和耗时，不记录完整路径/查询字符串、请求体、上传文件内容。未知路径显示 unmatched。日志过滤已配置密钥、URL 内嵌密码、Bearer 与常见 token/password 字段；异常只输出类型，响应只含通用错误，不输出原始 SQL 和堆栈中的输入。SQLAlchemy 隐藏 SQL 参数；HTTPX/HTTPCore 请求级日志默认关闭。该过滤是兜底，后续业务代码仍禁止主动日志输出敏感内容。

`.env`、运行数据、日志和构建产物由 .gitignore 排除，不能提交真实密码和原始文件。

## MinerU 与 BM25 实现补充（2026-11-13）

CLI 的实际 cwd 是每次租约独立目录，`MINERU_WORKING_DIR` 仅保留兼容旧配置；模型和配置仍使用绝对路径。Linux 本机各 Worker 共享 DATA_DIR 下的 flock 槽位限制并发，Windows Worker 未验证且暂不支持。默认超时 1800 秒同时作为等待名额上限；取得名额后解析再计时。ZIP 限制和产物支持格式见 [MinerU 接入](../infra/mineru/README.md)。

Linux CLI 由独立进程组的 guardian 启动，设置 parent-death signal；Worker 被 SIGKILL 后 guardian 会终止组内 MinerU 及子进程，避免恢复 Worker 与孤立解析进程同时占资源。普通解析超时也会杀掉该进程组。该机制已有真实进程树测试；MySQL 状态恢复仍靠持久租约与 outbox，不依赖操作系统进程存在。脱离原进程组自行 daemonize 的第三方子进程不在本机制保证范围内。

`ES_TIMEOUT_SECONDS=30` 控制单次 ES HTTP 超时，`ES_BULK_BATCH_SIZE=200` 控制每批分块数。默认 standard 分词与 ES 默认 BM25；未验证 IK 时不启用 IK。分块不跨页、不跨来源块，最多 1200 字符，ID 为 `文档ID:版本:页码:块序号:字符偏移`。保存直接关联的 image_keys 和同页关联的 page_image_keys，二者不等同于图像内容本身。查询只接受数据库确认的当前已完成版本，并与用户文档范围相交。`retrieval.retrieve` 是服务入口，已接入 `/api/search` 和问答接口；HTTP 层严格检查显式范围，不静默移除不可用项，并限定任务 index_name 与当前 ES_INDEX 一致。

每批 bulk 必须 HTTP 成功且所有 item 成功，refresh 后再通过 `_search` 检查本批全部 ID 可见；失败不得返回 TaskResult。产物已持久化且上述检查通过才允许 Worker 发布 completed 和 current_searchable_version。hybrid 任务明确失败为 RETRIEVAL_UNSUPPORTED，不伪称已建立向量索引。

## 问答预算与图片输入

图片校验、缩放、总张数、JPEG 副本字节数和图像 token 预算已执行，规则见 [API 图片资源](api.md#5-图片资源已实现)。总输入文本使用序列化消息 UTF-8 字节数 + 128 保守计费，包含系统指令、问题、来源元数据及图片标签；先按预算选择实际检索片段，最终发送前再次检查，不静默截断问题或用户图片。模型 usage 超过输入预算时保存失败，不返回超预算回答。超长单条片段无法纳入时也明确失败。不同图片处理器、长上下文与最大并发仍需专项服务器压力测试。

`IMAGE_MAX_CONTEXT_COUNT` 必须小于等于服务实际允许图片数。模型指南原命令上限 4 图，因此沿用该服务时设为 4；用户图片占满 4 个名额且检索有相关图时会提示预算不足，需减少用户图。默认配置 8 图不代表已通过最大多图验收。

模型请求通过 httpx 直接连接配置端点，禁用环境代理且不自动重试；所有模型图片都是服务端校验并缩放后的 JPEG data URL。新增运行依赖 httpx 0.28.1、Pillow 12.3.0，原图仍保存在项目 DATA_DIR。`LLM_VISION_ENABLED` 默认 false，启用前必须核对当前服务能力与限制。

## 前端能力配置与轮询

前端通过只读 `GET /api/capabilities` 获取视觉开关和 PDF/图片上传限制，不复制服务端阈值到浏览器环境变量。此接口只反映 Settings；`vision_enabled=true` 不表示模型实时可用，调用失败仍显示实际错误。关闭或返回 `VISION_NOT_READY` 时明确提示，可继续文字问答。接口失败显示能力未知，服务端校验仍为最终依据。

`VITE_POLL_INTERVAL_MS=3000` 控制文档列表和 pending 消息的轮询间隔，允许至少 1000 毫秒，非法值回退 3000。属于 Vite 构建时配置，修改后需重启开发服务器或重新构建。两个页面复用卸载时清理的轮询逻辑，使用 AbortController 取消在途请求，并防止异步初始化在离页后再创建定时器。浏览器通过现有 `VITE_API_BASE_URL` 访问 API，受控图片仍使用 API 返回的同源资源 URL；部署时保留 `/api/images/` 代理。
