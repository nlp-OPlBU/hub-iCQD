# MinerU CLI 接入

Worker 已实现 `mineru-kit parse <PDF绝对路径> --format zip -o <ZIP绝对路径> --tier <配置档位>`，以参数数组启动，不使用 shell。模型部署见 [模型指南](../../docs/models.md)。

`MINERU_COMMAND` 指定可执行程序；`MINERU_HOME`、`MINERU_CONFIG` 作为绝对路径传给子进程。cwd 和输出位于 `DATA_DIR/tmp/tasks/<task_id>/<lease_token>/`，避免多个任务及租约相互覆盖。`MINERU_WORKING_DIR` 保留配置兼容性，不再用作 CLI cwd。超时为 `MINERU_TIMEOUT_SECONDS`；超时杀死 Linux 进程组并回收。CLI 输出写临时文件，不在内存中无限积累。

Linux Worker 用 `DATA_DIR/tmp/mineru-slots/` 的 OS 文件锁实现 `MINERU_MAX_CONCURRENCY`（默认 1），同机多进程共享名额，退出自动释放；等待名额也有相同超时。所有本机 Worker 须共享 DATA_DIR 及并发配置。该实现使用 POSIX flock，Windows Worker 尚不支持；跨机器 GPU 协调不在此限制内。

ZIP 解压校验路径、符号链接、条目数、单文件及总大小、压缩比与 CRC。当前支持已实测的 MinerU 4.1.7 `structured_content.json` pages/blocks 格式，同级必须存在非空 Markdown；未知格式明确报 `ARTIFACT_INVALID`，不猜测页码或退化为无来源文本。`page_idx` 从 0 转成展示页码 1，文本、表格、图片说明和注释统一为文本块，校验图片引用在产物目录内且对应非空文件。保留 bbox 和图片存储标识，不暴露绝对路径。

校验通过后保存 `normalized.json` 与原始解压文件，fsync 后发布到 `parsed/<document_id>/v<version>/<lease_token>/`；不同租约目录互不删除。ES 失败时产物保留以便诊断。没有自动清理旧尝试目录，清理需结合数据库引用另行安排。

2026-11-13 basic CPU 六页样例通过完整 Kafka/Worker 异常矩阵；advanced 使用 CPU 小模型 + 本机 8104 专用 VLM 的六页样例也已完成真实队列处理和 ES 检索，约 62 秒、6 页、14 张图片关联。该端点是版本匹配的模型推理服务，不是 PDF HTTP API；当前模型有效窗口 8192。使用 `scripts/start_mineru_vlm.sh` 从统一配置和 MinerU YAML 启动，实际状态仍需检查 `/health`、`/v1/models`。默认档位不等于服务在线。详细记录见 [服务器验收](../../docs/server-acceptance.md)。

Linux CLI guardian 使用 parent-death signal：Worker 强杀后终止组内 MinerU 子进程；正常超时同样杀进程组。MySQL 租约和 outbox 仍负责恢复任务。可用 `MINERU_DEVICE_MODE=cpu`、`MINERU_OFFLINE=true` 与 `MINERU_CPU_THREADS=2` 复现本轮 CPU/离线小模型设置，GPU 共卡并发尚未压力验证。
