# MinerU CLI 接入

Worker 已实现 `mineru-kit parse <PDF绝对路径> --format zip -o <ZIP绝对路径> --tier <配置档位>`，以参数数组启动，不使用 shell。模型部署见 [模型指南](../../docs/models.md)。

`MINERU_COMMAND` 指定可执行程序；`MINERU_HOME`、`MINERU_CONFIG` 作为绝对路径传给子进程。cwd 和输出位于 `DATA_DIR/tmp/tasks/<task_id>/<lease_token>/`，避免多个任务及租约相互覆盖。`MINERU_WORKING_DIR` 保留配置兼容性，不再用作 CLI cwd。超时为 `MINERU_TIMEOUT_SECONDS`；超时杀死 Linux 进程组并回收。CLI 输出写临时文件，不在内存中无限积累。

Linux Worker 用 `DATA_DIR/tmp/mineru-slots/` 的 OS 文件锁实现 `MINERU_MAX_CONCURRENCY`（默认 1），同机多进程共享名额，退出自动释放；等待名额也有相同超时。所有本机 Worker 须共享 DATA_DIR 及并发配置。该实现使用 POSIX flock，Windows Worker 尚不支持；跨机器 GPU 协调不在此限制内。

ZIP 解压校验路径、符号链接、条目数、单文件及总大小、压缩比与 CRC。当前支持已实测的 MinerU 4.1.7 `structured_content.json` pages/blocks 格式，同级必须存在非空 Markdown；未知格式明确报 `ARTIFACT_INVALID`，不猜测页码或退化为无来源文本。`page_idx` 从 0 转成展示页码 1，文本、表格、图片说明和注释统一为文本块，校验图片引用在产物目录内且对应非空文件。保留 bbox 和图片存储标识，不暴露绝对路径。

校验通过后保存 `normalized.json` 与原始解压文件，fsync 后发布到 `parsed/<document_id>/v<version>/<lease_token>/`；不同租约目录互不删除。ES 失败时产物保留以便诊断。没有自动清理旧尝试目录，清理需结合数据库引用另行安排。

2026-11-13 basic CPU 六页样例通过应用解析、真实 MySQL 状态更新和临时 ES 8.15.3 BM25 检索。advanced 依赖本机专用 VLM 8104，尚未验收；该端点不是 PDF HTTP API。默认配置仍为 advanced，不能据此假定 8104 已启动。详细记录见 [验证记录](../../docs/verification.md)。
