# 部署与当前限制

2026-11-13 已完成真实 Kafka 完整链路、异常矩阵与项目独立 Nginx 部署，图文问答及真实浏览器上传流程通过；advanced 额外验证完成，服务器进程继续运行。**当前地址、资源边界、全部启动/复验步骤和限制以 [服务器验收与部署](server-acceptance.md) 为准**。共享配置加载、非 root 账号与开机托管尚未实施。以下保留开发入口与部署顺序参考。

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

环境材料中的基础设施启动命令保留为参考，不写入一键启动脚本；避免重复启动或改变共享服务。

上述入口已在本轮 Linux 服务器执行（PowerShell 入口仅静态检查）；Bash 为 UTF-8/LF，默认使用 conda `py312` 解释器，可通过 PYTHON 覆盖。systemd 模板仅供审阅，没有安装或启用。
