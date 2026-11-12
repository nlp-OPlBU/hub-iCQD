# 本地验收记录

## 2026-11-12 上传与查询验收

仅在 Windows 本地执行，未连接共享服务器，未启动 Kafka、MinerU、ES 或模型。已实现 PDF 批量上传、文档列表/详情、任务查询，以及文件系统/数据库失败补偿；未改数据库结构。

| 检查 | 实际结果 |
| --- | --- |
| Python 运行依赖 | 安装 python-multipart==0.0.33，pip check 通过，requirements.txt 和 requirements-lock.txt 已更新 |
| 后端 pytest（启用临时真实 MySQL 8.0.33） | **27 passed**，保留既有基础及迁移检查，新增 13 项上传/查询检查（含 1 项真实 MySQL 上传/回滚） |
| 批量追踪 | 同名 PDF 分配不同 UUID；原文件内容/SHA256、pending 任务、outbox payload 可逐项核对；列表、详情、状态、分页及参数/不存在错误通过 |
| 非法输入 | 扩展名、MIME、PDF 文件头/结束标记、空文件、单文件大小、数量、损坏 multipart 均拒绝；客户端路径不参与落盘路径 |
| 整个请求大小 | 实际累计字节包含 multipart 开销；无 Content-Length 和伪造较小 Content-Length 均能限流，拒绝前没有业务记录/原文件提交 |
| 事务与文件清理 | outbox 写入失败回滚文档及任务，并清理该文件，已提交项保留；SQLite 故障注入与真实 MySQL 均验证此路径 |
| COMMIT 与补偿 | SQLite 故障注入验证提交失败保留孤立文件/补偿记录、预览不删除、离线补偿删除孤立文件；回滚后原文件删除失败保留补偿记录，后续恢复清理；提交成功但确认丢失可重新确认并保留原文件；补偿记录删除失败不影响 accepted，后续补偿只移除记录 |
| 上传不解析 | 禁止创建子进程的上传测试通过；实际代码不调用 MinerU、Kafka、ES 或模型，outbox 仅入库 |
| 前端构建 | npm run build：vue-tsc 和 Vite 构建通过；本轮未实现业务 UI |

实际完整测试命令（在 backend 目录使用项目虚拟环境）：

```powershell
$env:RAG_TEST_MYSQL_BINARY = 'D:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'
python -m pytest -q -o faulthandler_timeout=45 --basetemp=<全新且不存在的可写英文绝对路径>
```

默认不设置 RAG_TEST_MYSQL_BINARY 时会跳过 2 项真实 MySQL 测试（本轮完整运行显式开启了真实 MySQL）。MySQL fixture 位于 tests/conftest.py，使用随机本机端口、隔离临时目录及随机密码，测试退出时关闭该测试实例，不连接 `.env` 数据库。普通单元测试使用临时 SQLite，不作为 MySQL 兼容性的替代。

Windows 沙箱阻塞 asyncio 创建内部 socket，导致 TestClient 等待；终止这三次未完成运行后，在获得工具放行的环境重跑成功。首次真实 MySQL 运行使用中文基目录时，mysqld 选择了错误的日志位置并因无权限退出（24 passed、2 errors）；改用已授权的英文绝对临时目录后 26 passed。最后补充文件删除失败补偿检查并修正提交结果确认的清理边界，完整重跑 **27 passed**。未修改现有 MySQL 服务或全局配置。原有 Starlette/AnyIO 弃用提示及 npm 用户级 python 配置提示仍存在。

补偿工具须在所有上传写入停止、在途事务结束后连接同一主库运行；尚未提供自动调度。完整 PDF 语法、MinerU 可解析性、并发资源和共享存储权限，以及 Kafka 投递/Worker 状态流转仍待后续服务器验证。

## 2026-11-11 基础工程验收（历史）

日期：2026-11-11（Asia/Shanghai）。仅验证当前 Windows 开发工作区；没有连接或修改共享服务器，没有部署模型或业务解析链路。

## 实际执行与结果

| 检查 | 结果 |
| --- | --- |
| 安装项目隔离 Python 依赖 | 成功，根目录 .venv；完整版本记录于 backend/requirements-lock.txt |
| 后端 pytest（开启临时真实 MySQL） | **14 passed**，含 13 个基础测试用例与 1 个真实数据库生命周期测试 |
| 配置 | `.env.example` 可加载；路径独立于 cwd；环境变量覆盖；敏感连接串及非法限制校验通过 |
| HTTP 与日志 | 健康接口、503 数据库未就绪、统一 404/422/500、request_id、错误不回显输入、日志脱敏通过 |
| MySQL 离线 SQL | 6 张业务表、utf8mb4、DATETIME(6)、循环外键延迟创建检查通过 |
| MySQL 8.0.33 真实迁移 | 空库 upgrade head、重复 upgrade、alembic check 无差异、downgrade base、再次 upgrade 均通过 |
| 数据约束 | 重复文档版本、非法任务状态、outbox 错配版本均被拒绝；中文/20000 个四字节字符消息可保存；新版本失败保留旧可检索版本 |
| 数据库就绪 | 迁移后 `/api/health/ready` 在 TestClient 返回 200 |
| 实际 TCP HTTP | 临时随机本机端口启动 `python -m app.main`，GET `/api/health` 与 `/api/openapi.json` 均返回 200；随后关闭该 API 进程 |
| 前端 | npm install 生成 package-lock.json；npm run build 的 vue-tsc 严格类型检查和 Vite 7.3.8 构建通过，输出 frontend/dist |

本机版本：Python 3.13.9、Node.js 20.20.2、npm 11.17.0、MySQL 8.0.33。数据库测试使用临时数据目录、随机 localhost 端口、独立测试数据库与随机密码，`--no-defaults` 避免读取现有配置；不使用 `.env` 的数据库连接。测试最终关闭该实例，未启动/停止现有 MySQL 服务。临时数据由 pytest 临时目录保留策略管理，不进入 Git。

执行时遇到 Windows 沙箱网络/临时目录写权限限制，依赖安装、构建和本机服务测试通过提权执行。pytest 默认临时目录还出现权限冲突，实际运行改用已授权工作区内全新且不存在的临时目录。第一次真实数据库测试发现 PyMySQL 将 CHECK 违规映射为 OperationalError（3819），已修正断言后重新通过。

当前有一条第三方弃用提示：Starlette TestClient 引用 AnyIO 的 BlockingPortal 旧别名；不影响测试结果。npm 还提示用户级 `python` 配置将来不再受支持，本任务未更改用户级 npm 配置。

## 复现方式

激活项目虚拟环境后，在 backend 执行：

```powershell
python -m pytest -q
```

默认没有 RAG_TEST_MYSQL_BINARY 时为 **13 passed, 1 skipped**；真实数据库测试不会偷偷使用现有 `.env` 数据库。要启用本机专用临时实例：

```powershell
$env:RAG_TEST_MYSQL_BINARY = 'D:\Program Files\MySQL\MySQL Server 8.0\bin\mysqld.exe'
python -m pytest -q
```

可执行路径按实际安装位置设置。在默认临时目录不可写时，可提供全新且不存在的可写绝对目录 `--basetemp=<new-path>`；pytest 会清理指定基目录，所以不要指向现有项目、数据或共享目录。不要让机器已有 MySQL 的 data 目录成为测试基目录。

在 frontend 执行 `npm ci` 后执行 `npm run build`。Bash 启动脚本没有在 Linux 服务器运行，其入口命令的基础功能已在 Windows 直接执行验证。

## 尚未实现和待服务器确认

- 安全解压、人工重试接口、后续状态流转 repositories 与 Worker。
- Kafka outbox 投递、幂等消费者、自动重试调度、租约恢复及失败 Topic。
- MinerU、ES 写入/检索、Redis、Embedding/Reranker 和视觉 LLM 调用。
- 图片资源接口、会话/消息 API、真实图文问答及完整业务前端；这些路由尚未注册。上传/文档/任务路由已实现。
- 共享服务器的 MySQL 版本/账号/库与迁移、组件端口/权限、模型能力和 GPU 预算、Nginx 接入。

上传大小/数量限制已在运行时执行；ZIP、重试、租约和图像预算已定义与配置校验，其业务实现仍待完成。本地验收通过不代表完整 RAG 系统已实现或服务器联调成功。
