# 部署规划与当前限制

目前所有启动脚本、依赖清单、前端构建配置、SQL 和 Nginx 配置均为占位，不能直接运行。此文描述后续部署顺序，不代表已部署。

1. 在服务器只读确认软件版本、进程状态、端口、权限、认证、GPU 资源以及 MinerU 与模型能力，记录至环境说明。
2. 在服务器选定项目目录，准备隔离的 Python 环境与前端依赖；补齐并固定依赖版本。
3. 将 `.env.example` 复制为服务器本地 `.env`，填写真实连接信息；创建项目专属 MySQL 数据库与账号、Kafka Topic、ES 索引，确认数据目录可被 API 与 Worker 共同访问。
4. 完成并验证 MinerU CLI 样例解析和 vLLM 图文调用；Embedding 服务未配置时明确使用 BM25 模式。
5. 实现并分别启动 API、outbox 投递器、PDF Worker；业务 API 规划使用 `8001`，vLLM 保持现有 `8000`。
6. 构建前端，在审核现有站点路由后配置项目专属 Nginx 片段，处理 `/api/`、受控资源访问、上传体积及请求超时。配置校验通过后再按实际部署授权加载。
7. 执行真实 PDF 上传、状态、重试和图文问答联调；随后根据服务器情况增加进程托管。当前不预设服务器具备某一种进程管理器。

## 脚本位置（当前均为空）

| 文件 | 后续职责 |
| --- | --- |
| `scripts/check_environment.sh` | 只读检查命令、服务连通性与版本，输出脱敏摘要 |
| `scripts/init_mysql.sh` | 按显式配置初始化项目数据库，避免修改共享数据库 |
| `scripts/start_api.sh` | 启动项目 API，不启动共享基础设施 |
| `scripts/start_dispatcher.sh` | 启动 outbox 投递器 |
| `scripts/start_worker.sh` | 启动 PDF 消费者 |
| `scripts/build_frontend.sh` | 构建前端静态产物 |
| `scripts/dev.sh`、`scripts/dev.ps1` | 项目开发辅助入口，不假设 Windows 有服务器软件 |

环境材料中的基础设施启动命令保留为参考，不写入一键启动脚本；避免重复启动或改变共享服务。
