# 部署与当前限制

运行日志路径、查看和排错命令见 [日志指南](logging.md)。

2026-11-13 已完成真实 Kafka 完整链路、异常矩阵与项目独立 Nginx 部署，图文问答及真实浏览器上传流程通过；advanced 额外验证完成，服务器进程继续运行。**当前地址、资源边界、全部启动/复验步骤和限制以 [服务器验收与部署](server-acceptance.md) 为准**。共享配置加载、非 root 账号与开机托管尚未实施。以下保留开发入口与部署顺序参考。

## 一键启动当前部署（Linux）

在项目根目录运行：

```bash
bash start.sh             # 后台启动缺失模块；已启动模块显示提示并跳过
bash start.sh --check     # 只读检查，不启动服务
```

入口为 `start.sh` → `scripts/start_project.py`，默认解释器为 conda py312，可用 `PYTHON=/path/to/python` 覆盖。配置选择顺序为 `--env-file` > `RAG_ENV_FILE` > 已存在的 `runtime/deployment/project.env` > 根目录 `.env`；相对路径基于项目根目录，不通过 shell source 配置。可从任意工作目录调用。

脚本适用于**已初始化的部署**，依次检查项目 MySQL、Kafka、ES 项目索引、问答模型、advanced 所需 MinerU 模型、API、投递器、Worker 和项目独立 Nginx。健康服务输出 `[已启动，跳过]`；API/模型/数据库使用实际就绪检查，投递器和 Worker 仅确认同一项目目录、同一配置文件的进程存活，业务依赖另行汇总。服务进程已存在但尚未就绪时等待，不创建第二份；端口被占用但不能确认就绪时报告失败，不终止占用进程。checkout 内文件锁防止多个一键启动同时执行。

自动启动范围是既有项目私有 MySQL（仅 loopback、非 3306、匹配 `project-mysql.json`，绝不重新初始化）、本机模型和应用。Kafka/ES 是共享依赖，仅检查 broker 和两个已存在的项目 Topic、ES 既有索引的可检索性；离线或资源缺失时报告失败，不启动共享实例。当前 BM25/关闭重排模式不启动 Redis、Embedding、Reranker；basic 档不启动 MinerU VLM。未初始化的数据库、迁移和 Topic 仍按原部署步骤准备；脚本不安装依赖或自动下载模型。Nginx 配置缺失时才使用现有模板生成独立配置，需要提前构建 `frontend/dist`。

```bash
# 指定配置及每个模块的就绪等待上限；模型冷启动可能耗时数分钟。
bash start.sh --env-file runtime/deployment/project.env --timeout 600
# 自定义路径（已有 Nginx 配置的 listen 须与 --web-port 一致）。
bash start.sh --mysql-directory runtime/deployment/mysql \
  --mysql-binary /usr/sbin/mysqld \
  --nginx-config runtime/deployment/nginx/nginx.conf --web-port 18080
```

新启动进程脱离终端继续运行，stdout/stderr **追加**到 `runtime/deployment/logs/{mysql,vlm,mineru-vlm,api,dispatcher,worker,nginx}.log`，可用 `--log-dir` 更换；已经运行的进程保留原日志位置。MySQL 自身错误日志及 Nginx access/error 仍在各自配置目录。脚本不覆盖原日志，不执行共享 Nginx reload。就绪等待期间每 15 秒提示一次；返回码 0 表示全部必要检查通过，1 表示有未就绪模块，130 表示检查被中断。失败/超时/中断保留已启动进程，排错后可重复执行；Ctrl-C 只停止启动检查，不负责停止后台服务。此入口不注册开机服务，也不替代长期进程托管或业务验收。

## 安全停止项目（Linux，保留模型）

```bash
bash stop.sh --check       # 仅预览将停止的模块和 PID；也可用 --dry-run
bash stop.sh               # 按顺序优雅退出，已停止的模块提示跳过
bash stop.sh --timeout 120 # 每个模块最多等待 120 秒；超时不强杀
```

入口 `stop.sh` → `scripts/stop_project.py` 与一键启动使用相同的解释器、配置选择规则和 checkout 文件锁；启动与停止不能同时执行。可通过 `--env-file`、`--mysql-directory`、`--mysql-binary`、`--nginx-config`、`--web-port` 与自定义启动配置保持一致，MySQL 目录与 Nginx 配置必须在当前项目内。

停止顺序为**项目独立 Nginx → API → outbox 投递器 → PDF Worker → 项目私有 MySQL**。Nginx master 收到 SIGQUIT 等待现有连接结束；其他模块收到 SIGTERM。API 等待当前请求收尾，Worker 完成当前任务、保存结果并处理 offset 后退出，最后才停止数据库。默认每个模块等待 `MINERU_TIMEOUT_SECONDS + 120` 秒（默认 1920 秒），等待期间每 15 秒提示进度。任一模块超时或检查失败就中止后续停机，返回非零，不发送 SIGKILL，保留后续模块及 MySQL；可排错后重跑。Ctrl-C 只中断等待，已经收到信号的模块可能仍在收尾。

**全部模型部署服务保留**，包括问答 vLLM、MinerU 专用 VLM、Embedding、Reranker 及其引擎子进程。共享 Kafka、ES、Redis、MySQL 和共享 Nginx 同样不在停止范围。不按软件名批量 kill、不按端口直接找进程杀掉，也不向整个进程组发信号；不删除原文件、解析产物、日志、Topic、索引或数据库。

脚本先验证整个停止列表，再发送第一个信号。应用须匹配 Python 可执行文件、模块、项目 backend 工作目录与配置文件；项目 Nginx 须匹配独立配置路径和可执行文件；MySQL 须同时匹配 `project-mysql.json`、可执行文件、loopback 端口、`--no-defaults`、独立数据目录与 PID 文件参数，**不凭 PID 文件内容选进程**。使用 Linux pidfd 固定进程身份，避免 PID 被复用时误发信号；当前 py312 缺少 Python/glibc 的 pidfd 包装，通过 Linux x86_64/aarch64 系统调用适配，缺少支持时拒绝发送信号。数据库停止前还检查当前 checkout 是否仍有其他配置的应用进程，存在则保留数据库。

此入口支持 `start.sh` 或各独立启动入口的项目进程。若检测到旧 `scripts/dev.sh` / `run_project.py` 监督器，预检拒绝停止其子进程，因为旧监督器会在子进程退出后触发其 60 秒强制清理；需单独安排监督入口停机。外部托管器的自动重启策略也应由对应托管器管理，本脚本不修改系统服务。

预览返回码 0 表示目标已核实、没有发出信号；实际停止返回码 0 表示所选项目模块均已停止或原本未运行，1 表示未完成/拒绝，130 表示中断。尚未停止时，模型和共享服务是否在线不影响该停机流程。

## 本地基础工程

在项目根目录使用系统 conda 环境 `py312`（`/usr/local/miniconda3/envs/py312`，Python 3.12；本机验收 3.12.11）；仅第一次且没有 `.env` 时复制 `.env.example`：

```bash
conda activate py312
python -m pip install -r backend/requirements-dev.txt
cp -n .env.example .env
cd backend
python -m pytest
python -m app.main
```

`scripts/start_api.sh` 和 `scripts/init_mysql.sh` 默认使用该环境的解释器，也可通过 `PYTHON=/path/to/python` 覆盖。

API 从根目录 `.env` 读取 APP_HOST/APP_PORT，默认 `127.0.0.1:8001`。访问 `http://127.0.0.1:8001/api/health`、`/api/docs`。没有数据库也可做进程健康检查；需要数据库就绪时，先按 [database.md](database.md) 创建项目专用数据库/账号，配置 MYSQL_* 并执行 `python -m alembic upgrade head`，再调用 `/api/health/ready`。开发自动重载可用 APP_DEBUG=true。

数据库迁移完成且 DATA_DIR 可写后，可以在 `/api/docs` 调用 `POST /api/documents/batch` 上传多个 PDF，再通过文档列表/详情和任务接口追踪。逐文件结果在 HTTP 200 的 results 中，须分别检查 outcome；可另开终端分别运行 `scripts/start_dispatcher.sh` 与 `scripts/start_worker.sh`，使 pending 任务经 Kafka 进入 Worker；实际运行需要可用的 MySQL、Kafka、MinerU、ES 和共享 DATA_DIR。新依赖 python-multipart 需按更新后的清单安装。补偿预览及停止所有上传写入后的清理步骤见 [database.md](database.md#上传文件补偿)。

另一个终端在 frontend 目录执行（Node.js 20.19+ 或 22.12+）：

```powershell
npm ci
npm run build
npm run dev
```

构建输出 frontend/dist；开发服务器通过 VITE_DEV_API_TARGET 将 `/api` 转发到 API，生产环境则由 Nginx 提供同源转发。页面显示实际健康请求结果，文档页与图文问答页已接入 API，交互与能力配置见 [API 契约](api.md#8-前端能力与限制配置已实现)，验证范围见 [验收记录](verification.md)。构建成功不表示外部业务服务就绪。npm run preview 仅预览静态构建，不作为生产反向代理。

使用 `backend/requirements-lock.txt` 可复现本次验收的完整 Python 依赖；常规运行/开发依赖分别为 requirements.txt 和 requirements-dev.txt。依赖升级后重跑验证并更新锁文件。

## 部署顺序参考

用户已确认单卡 48GB 显存、CPU 20 核、内存 90GB。全本地模型的选型、服务器保存位置、环境准备、下载、vLLM 启动命令、MinerU 配置和验收步骤统一见 [模型部署指南](models.md)。已提供两个模型启动入口；执行前须确认相应端口未被已运行的项目进程占用。

1. 在服务器只读确认软件版本、进程状态、端口、权限、认证、GPU 资源以及 MinerU 与模型能力，记录至环境说明。
2. 在服务器选定项目目录，准备隔离的 Python 环境与前端依赖，使用已提交的依赖清单和锁文件并验证服务器兼容性。
3. 将 `.env.example` 复制为服务器本地 `.env`，填写真实连接信息；创建项目专属 MySQL 数据库与账号、Kafka Topic、ES 索引，确认数据目录可被 API 与 Worker 共同访问。
4. 按模型指南准备项目专属权重与隔离环境，逐个验证图文问答（8100）、Embedding（8102）、Reranker（8103）、MinerU 专用 VLM（8104）及 CLI 解析。图文向量与 ES 索引未验证前使用 BM25，重排保持关闭。
5. 分别启动已实现的 API、outbox 投递器、PDF Worker；业务 API 使用 `8001`。原有 `8000` 不自动调整，若占用同一 GPU 必须计入共卡预算。Worker 继承项目 `MINERU_HOME`，通过 CLI 调用本地模型。
6. 构建前端，在审核现有站点路由后配置项目专属 Nginx 片段，处理 `/api/`、受控资源访问、上传体积及请求超时。配置校验通过后再按实际部署授权加载。
7. 执行真实 PDF 上传、状态、重试和图文问答联调；随后根据服务器情况增加进程托管。当前不预设服务器具备某一种进程管理器。

## 脚本位置

| 文件 | 当前职责 |
| --- | --- |
| `start.sh`、`scripts/start_project.py` | 一键检查并后台启动缺失的项目模块，已启动则跳过；`--check` 只读，日志追加，失败返回非零 |
| `stop.sh`、`scripts/stop_project.py` | 顺序优雅停止项目应用及私有 MySQL，保留全部模型和共享服务；`--check`/`--dry-run` 只预览，超时不强杀 |
| `scripts/check_environment.sh` | 已实现：只读检查命令、TCP/HTTP 连通性与版本，输出脱敏摘要，不启动服务 |
| `scripts/init_mysql.sh` | 已实现：对配置中的已有项目数据库执行 Alembic upgrade head，不创建账号或共享服务 |
| `scripts/start_api.sh` | 已实现：启动基础 API，不启动共享基础设施 |
| `scripts/start_dispatcher.sh` | 已实现：启动 outbox 投递器 |
| `scripts/start_worker.sh` | 已实现：启动 PDF 消费者 |
| `scripts/build_frontend.sh` | 已实现：npm ci + npm run build |
| `scripts/dev.sh` | API/投递器/Worker 的前台监督与退出清理，可选独立 Nginx；不启动共享基础设施 |
| `scripts/dev.ps1` | Windows API 开发入口；Worker 的 Linux flock/进程保护不冒充 Windows 支持 |
| `scripts/start_vlm.sh`、`scripts/start_mineru_vlm.sh` | 从配置启动本地问答模型和匹配 MinerU 版本的专用 VLM |
| `scripts/render_nginx.py` | 生成 server.conf 和独立 prefix 配置；不复制到共享目录或 reload |
| `infra/kafka/topics.sh` | 读取同一配置，幂等创建两个项目 Topic，不删除/更改其他 Topic |
| `scripts/start_private_mysql.py` | 可选的隔离原生 MySQL；显式新目录/非 3306 端口，不读全局配置；不是默认共享实例启动器 |

环境材料中的共享基础设施启动命令保留为参考，不写入一键启动脚本；项目私有 MySQL 仅复用带标记的既有目录。

上述入口已在本轮 Linux 服务器执行（PowerShell 入口仅静态检查）；Bash 为 UTF-8/LF，默认使用 conda `py312` 解释器，可通过 PYTHON 覆盖。systemd 模板仅供审阅，没有安装或启用。

## Reranker 上线

一键启动已支持配置启用的 8103 Reranker；启动入口 `scripts/start_reranker.py` 使用本地权重和与 vLLM 版本匹配的模板。先验证 `/score` 真实图文输入，再启用 RERANK_ENABLED 并优雅重启项目 API；故障显式回退召回排序。配置、验收与回退见 [图文重排](reranking.md)。
