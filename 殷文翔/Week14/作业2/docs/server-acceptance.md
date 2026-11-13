# 服务器部署与完整链路复验

> 2026-11-13 图文重排更新：8103 Reranker 已部署，已接入混合候选评分与 RAG，真实图文问答及显式降级已验证。此前“重排未接入/待部署”描述由[图文重排说明](reranking.md)覆盖；并发与开机托管仍未验收。

> 2026-11-13 向量检索更新：本机 8102 Embedding 已部署，真实文本/图片 2048 维、历史回填及 BM25+向量 RRF 混合召回已验证；本页较早的“Embedding 待部署/仅支持 BM25”记录由[向量检索与回填说明](vector-retrieval.md)覆盖。Reranker 仍未接入。

日期：2026-11-13，服务器项目根目录为 `/workspace/hub-iCQD/殷文翔/Week14/作业2`。本次在服务器执行真实服务联调，使用项目专属资源；没有修改或重载共享 Kafka、ES、MySQL、Nginx 配置，也没有修改根目录原 `.env`。

## 首个里程碑

已验证：浏览器/HTTP 批量 PDF → MySQL 文档、任务、outbox 同事务 → 投递器 → 真实 Kafka → 独立 Worker → 真实 MinerU basic → 安全解压、持久化产物 → ES BM25 → MySQL completed → HTTP 检索。API 实际查询到 pending、processing、completed、failed，失败 Topic 已实际收到失败事件。

后端完整链路测试还覆盖：Kafka 连接失败后同一 outbox 事件补偿；Worker 在 processing 后被 SIGKILL，租约过期重新投递后成功；已完成消息重复投递并确认消费组 offset 前进而 attempts/产物不变；真实 CLI 一秒超时；专属错误 mapping 导致 ES bulk item 拒绝；失败记录、finished_at 与失败 Topic 持久落地；旧版本 chunk 保留在 ES 时，只能检索完成的新版本。

真实图文问答限定本次上传的文档。问题“图中底部流程中间的方框内写着什么？购物车最多可以添加多少件商品？”得到“问答技能”“100 件”，有真实页码和受控图片来源。观察代理原样转发模型请求，验证 data URL 可解码为实际图片；三次模型请求的图片数量为 3、1、4（文字问题的文档图；图片辅助检索的用户图；最终用户图和文档图），不是路径或文件名替代图片。相同幂等键重放没有新增模型请求。

报告：`runtime/pipeline-acceptance/ab9390aa5a6d413abf1d12d3e56e3444/report.json`，状态 passed。同目录保存各阶段项目进程日志及解析产物，均被 Git 忽略。该测试的临时数据库、专属 Topic/消费组、两个测试索引及项目 API/Worker/Nginx 均已清理，已有共享服务继续运行。

## 当前项目部署

| 组件 | 当前部署 |
| --- | --- |
| 配置 | `runtime/deployment/project.env`，0600，独立于原 `.env`；真实密码不写本文 |
| MySQL | 项目独立原生实例，`127.0.0.1:13306`，8.0.46；专属库 `rag_ywx_week14`，业务账号 `rag_app`，已执行 Alembic head |
| Kafka | 复用在线 9092；仅创建 `multimodal-rag-ywx-week14.parse`、`.failed`，各 3 分区；消费组 `.workers` |
| ES | 复用在线 9200，8.15.3；专属索引 `multimodal-rag-ywx-week14-chunks-v1`，standard/BM25 |
| 应用 | API `127.0.0.1:8001`、独立 dispatcher 和 Worker，由 `scripts/dev.sh` 前台监督；任一子进程退出后整体退出，供托管器重启 |
| 页面 | 独立 Nginx `127.0.0.1:18080`，配置 `runtime/deployment/nginx/nginx.conf`，静态页面与同源 `/api/` 已验证 |
| 图文模型 | Qwen3.5-4B，`127.0.0.1:8100/v1`，实际 ID `rag-vlm`，16384 窗口、4 图上限；应用显式开启视觉、上下文图片数 4 |
| MinerU 专用模型 | `127.0.0.1:8104`，实际 ID `mineru-vlm`，有效窗口 **8192**；16384 被当前版本拒绝，未强制绕过模型限制 |
| 解析档位 | 当前部署显式配置 advanced；真实六页样例约 62 秒完成，产物含 14 张图片关联，错误为空、ES 检索通过 |
| 数据 | `runtime/deployment/data`，API 与 Worker 共用；私有 MySQL 数据在 `runtime/deployment/mysql` |

共享 3306、80 没有启动；没有对共享服务执行 reload/restart，也没有注册系统服务或创建 OS 账号。18080 仅监听服务器本机，可通过已有 SSH 通道访问；没有配置公网域名、TLS 或外网入口。本轮原生进程在当前服务器会话运行，**尚未配置开机恢复**，不能把它当作高可用生产托管。

项目部署已通过 HTTP 上传两份 test2.pdf；另一次真实 Chromium 测试从页面上传 `browser-batch-a.pdf`、`browser-batch-b.pdf`，轮询至完成，再提交原图和文字问题，展示真实来源和图片，截图 `frontend/test-results/server-pipeline.png`。这些文档和会话保留在专属部署数据库中供检查。

最终状态快照 `runtime/deployment/status.json`（08:19 UTC）：页面和 ready 均为 200；部署库 7 份文档/任务均 completed、7 个 outbox 均 published；项目 ES 索引 361 个分块，消费组各分区已提交 offset 合计 7。独立异常测试的 Topic 已确认移除；部署专属资源继续保留运行。

advanced 追加报告 `runtime/deployment/advanced-report.json` 保存状态 pending → processing → completed、耗时、页数/图片数、检索来源和真实图文回答。该六页样例使用 CPU 小模型 + 8104 专用 VLM；Qwen 问答服务同时驻留，当前两模型 GPU 合计约 24GB。完整异常矩阵使用 basic；真实浏览器分别在 basic、advanced 部署执行通过，advanced 约 2.3 分钟。浏览器每次生成唯一文件名，允许重复复验；截图取最后一次结果。这些实测不替代压力测试。

追加图文检查发现模型有时写“检索证据 S1”而不加方括号。已将这种明确来源引用规范为 `[S1]` 后继续验证；没有推断新的来源，未知编号和代码字面量仍按原规则处理。修复后 advanced 图文回答引用真实第 1、2 页，原始不足回答和诊断输出也保留在报告中。

## 分阶段复验命令

以下命令均在项目根目录执行，使用系统 conda py312。不要对共享实例重新执行初始化或根据本文启动共享软件。

```bash
# 只读摘要：TCP 可达不等于认证/业务就绪；不创建资源。
RAG_ENV_FILE=runtime/deployment/project.env bash scripts/check_environment.sh

# 回归：显式启动并清理独立临时 MySQL，不连接 .env 的库。
cd backend
RAG_TEST_MYSQL_BINARY=/usr/sbin/mysqld \
  /usr/local/miniconda3/envs/py312/bin/python -m pytest -q
cd ../frontend
npm run build
LD_LIBRARY_PATH="$PWD/../runtime/browser-libs/extracted/usr/lib/x86_64-linux-gnu" npm run test:ui
cd ..
```

本次回归 **111 passed, 3 skipped**。三项跳过分别是显式开关关闭的完整 pipeline、ES/模型应用测试，以及只在真实 MySQL 执行的行锁并发测试的 SQLite 分支。完整 pipeline 另行开启并通过 **1 passed，188.48 秒**；前端构建通过，接口模拟浏览器 **18 passed**，真实部署浏览器 **1 passed**。跳过项不表示已在本次回归中执行。

完整 pipeline 必须事先有可用的真实 Kafka、ES 和本地视觉模型；测试自行创建专属 UUID 资源和独立 MySQL，不启动共享服务，不修改部署库。约 3 分钟，PDF/图片和问题断言针对六页 test2.pdf：

```bash
cd backend
RAG_TEST_MYSQL_BINARY=/usr/sbin/mysqld \
RAG_PIPELINE_PDF=/workspace/pdf/test2.pdf \
RAG_PIPELINE_KAFKA=127.0.0.1:9092 \
RAG_PIPELINE_ES=http://127.0.0.1:9200 \
RAG_PIPELINE_LLM=http://127.0.0.1:8100/v1 \
  /usr/local/miniconda3/envs/py312/bin/python -m pytest -x -q tests/test_pipeline_live.py
cd ..
```

真实浏览器检查会向指定项目部署新增两份文档和一段会话，不模拟 API 或模型：

```bash
RAG_SERVER_URL=http://127.0.0.1:18080 \
RAG_SERVER_PDF=/workspace/pdf/test2.pdf \
RAG_SERVER_IMAGE="$PWD/data/tmp/mineru-verify/extract/images/page_0_image_5.jpg" \
LD_LIBRARY_PATH="$PWD/runtime/browser-libs/extracted/usr/lib/x86_64-linux-gnu" \
  npm --prefix frontend run test:server
```

## 项目进程启动与停止

安全停止入口为 `bash stop.sh`，预览为 `bash stop.sh --check`；保留全部模型部署与共享服务，先让应用退出，再停止私有 MySQL，超时不强杀。适用范围、参数及旧监督器限制见 [安全停止](deployment.md#安全停止项目linux保留模型)。本次新增入口只进行隔离测试和真实进程预览，没有实际停止当前部署。

已初始化部署可使用 `bash start.sh` 自动跳过已运行模块并启动缺失的项目服务；先查看状态用 `bash start.sh --check`。新启动进程后台运行并追加到独立日志，参数及限制见 [一键启动](deployment.md#一键启动当前部署linux)。下方保留各组件手工启动/监督入口，不要与一键启动重复运行。

当前各模块日志位置、tail/rg 查看方法、任务排错及重启时保留日志的命令见 [日志指南](logging.md)。直接运行下面的启动入口默认输出到终端，文件日志需按指南重定向。

当前已有进程时不要再次启动同端口。下面是停机后重启的独立入口；每条长期运行命令应置于各自终端或经授权的托管器中。配置文件由程序读取，不要用 shell `source .env`。

```bash
export RAG_ENV_FILE=runtime/deployment/project.env

# 仅重启已经初始化的项目私有实例，不带 --initialize。
/usr/local/miniconda3/envs/py312/bin/python scripts/start_private_mysql.py \
  --directory runtime/deployment/mysql --binary /usr/sbin/mysqld

# 模型各自独立启动；会占用 GPU，先只读确认端口和资源。
bash scripts/start_vlm.sh
bash scripts/start_mineru_vlm.sh

# 数据库已经存在时升级；Topic 创建幂等，仅操作配置中的两个 Topic。
bash scripts/init_mysql.sh
bash infra/kafka/topics.sh

# 审阅输出，不修改共享配置；生成后先 nginx -t。
/usr/local/miniconda3/envs/py312/bin/python scripts/render_nginx.py \
  --output-dir runtime/deployment/nginx
nginx -t -p "$PWD/runtime/deployment/nginx/" -c "$PWD/runtime/deployment/nginx/nginx.conf"
bash scripts/dev.sh --nginx-config runtime/deployment/nginx/nginx.conf
```

Ctrl-C/SIGTERM 停止对应前台项目进程；监督脚本先通知 API/dispatcher/Worker/Nginx 正常退出，最多等待 60 秒，再终止未退出子进程。Linux Worker 被强杀时，CLI guardian 会终止整个 MinerU 进程组，MySQL 租约仍由 outbox 投递器恢复。已生成的产物和原文件不会因进程停止而删除。私有 MySQL 与模型使用各自入口单独停止；不要用按软件名批量 kill 的命令。

首次新建私有 MySQL 必须使用**全新目录**与 `--initialize`，要求配置为 127.0.0.1、非 3306 端口、非 root 业务账号和非空密码。工具使用 `--no-defaults`、独立 socket/PID/data，拒绝已有目录/占用端口；管理员随机密码仅保存在项目私有 `admin.secret`（0600）。现有部署绝不能重新初始化。数据备份、迁移到长期 MySQL 与管理员密钥保管仍需运维安排。

`infra/systemd/multimodal-rag.service.example` 是可审阅的托管模板。替换服务账号/路径并安排数据目录权限后，才能按部署授权安装、daemon-reload、enable/start。Nginx 的 `server.conf` 同样只是候选片段；并入共享 http 配置和 reload 需要明确授权，当前只运行独立 prefix 的 Nginx。

## 限制

- 本次完整异常矩阵以 MinerU **basic/CPU** 执行；advanced 的额外部署验证另见 verification.md，不能将 basic 的耗时和质量套用于 advanced。
- 检索为 BM25，Embedding/Reranker 未接入；Redis 不参与任务持久状态，也不是此里程碑依赖。
- 单台服务器、单 Worker、低并发样例通过，不代表 GPU 高并发、最大图片/请求、长上下文或大 PDF 压力验收。当前部署进程以 root 运行；非 root 服务账号、目录权限与开机托管尚未落实。
- API 尚无鉴权/多租户；当前绑定 loopback。公网发布前需要单独落实鉴权、TLS 和站点边界。
- 旧版本索引和崩溃/失败产物采用逻辑隔离，尚无自动回收与磁盘保留策略；图片上传 COMMIT 不明可能遗留孤立文件。畸形 Kafka 消息仅告警后提交，未设计独立的持久化无关联消息死信仓库。
- 过期但仍存活的 Worker 在同一任务版本下并发写 ES 的全面隔离尚未专项验证；已有数据库租约令牌和版本保护不等于跨 MySQL/ES 的原子提交。
- py312 仍有已知 MinerU `openai<3` 与 openai 3.48.0 依赖声明冲突；本轮未更改共享 Python 依赖，实际 basic/模型用例通过不能视作冲突已解决。

## 图文重排本机验收追加

8103 `rag-reranker` 已常驻本机进程，专属环境配置已启用 RERANK_ENABLED，本项目 API 已优雅重启。`bash start.sh --check` 全部必要模块通过；`http://127.0.0.1:18080/#/chat` 提供已集成的图文问答。真实 Nginx Chromium 重排专项 1 passed：Mate40 图片问题回答 6999、来源第 4 页可打开、图片实际加载、无依据问题无来源拒答。详细证据、预算、回退步骤和限制见 [图文重排](reranking.md) 与 [验证记录](verification.md)。
