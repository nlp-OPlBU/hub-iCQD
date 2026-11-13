# 验收记录

## 2026-11-13 检索与多模态问答应用验收

已实现 BM25 文本检索 → 指定文档范围 → 回答与来源，并接入用户图片辅助检索、最终问题/用户图/文档图共同输入、受控图片资源、MySQL 会话/消息/来源持久化和 Vue 问答页面。沿用 0001_initial 表结构，无新迁移；生产常驻部署另行安排。

本轮使用独立临时 MySQL（显式 RAG_TEST_MYSQL_BINARY，随机本机端口及专属测试库）、独立 ES 8.15.3（19200/19300，runtime/rag-acceptance/es，512MiB 堆）和临时 Qwen3.5-4B vLLM（8100，rag-vlm，16K 窗口，BF16，GPU 比例 0.32，每请求最多 4 图）。未启动共享 MySQL 3306、ES 9200、Kafka、Redis、Nginx，未修改共享服务配置或项目 .env。结束后临时 MySQL/ES/vLLM 已停止，8100/19200/19300 已释放，GPU 回到 1MiB；没有留下常驻业务 API 或模型。

实际结果：

- 文本链路：问题“购物车最多可以添加多少件商品？”限定 test2.pdf 时回答 **100 件**，来源 S1 为真实第 2 页分块；同一问题限定专属对照文档时回答 **999 件**、来源为对照文档第 1 页，没有越界检索。不存在或未完成范围、空范围会拒绝；随机无命中查询返回“依据不足”、空来源。
- 用户图片来自已有 MinerU basic 六页样本的 page_0_image_5.jpg，原图 1359×473、107952 字节。询问“图中底部流程中间的方框内写着什么？购物车最多可以添加多少件商品？”，模型读出 **问答技能** 和 **100 件**；来源页码/块 ID 与真实 ES 分块逐项核对，相关图片的元数据和二进制资源接口均成功读取。初次复验使用含糊的“蓝色框”问法，模型读取了另一处蓝色虚线区域，因此改为指明“底部流程中间方框”，问句未包含期望答案。
- 无文字、仅用户图片时，先用真实图像提取检索文本，最终回答说明问答技能工作机制，引用实际第 1 页 S1/S2 与相关图片。
- 实际模型 HTTP 请求共 6 次，图像内容块数量依次为 **0、0、1、3、1、2**：两次纯文本回答；两次各包含“图片辅助检索 + 最终图文回答”。拦截仅观察并转发真实请求，未替换模型响应。每个 data URL 被 Base64 解码并经 Pillow 验证像素尺寸，报告保留字节数/尺寸/SHA256，不把文件名或路径当图像。原图模型副本为 69069 字节 JPEG，相关文档图片副本为 89271 字节等；均受尺寸、总字节、数量和 token 预算控制。
- 同 key 重放返回同一消息对，不增加模型请求；10 条消息、顺序、实际文档版本快照和完整来源 JSON 经真实 MySQL 读回核对。测试库及专属 ES 索引完成后移除；原始样本副本与报告保留在 Git 忽略目录 `runtime/rag-acceptance/`，不提交用户文件、解析产物或运行数据。
- 最新成功报告 `runtime/rag-acceptance/report.json`，run_id `6d5b500c-9ddd-4871-a0f1-1be20053e2ed`，conversation `d88ecce2-e9b4-41ea-bf84-114fd7af5548`。本轮 test2 文档为 `53958ae6-77ac-4c52-aea4-7545a0f974ad`，对照为 `35d0d777-2775-42b5-b694-7e3ee8a56e81`；此为可丢弃验收数据，不是生产文档身份。

自动检查：

- py312 执行下面完整命令：**79 passed, 1 skipped**；包含已有上传/Worker 回归、SQLite 与真实 MySQL 双后端问答接口、模型真实 HTTP 图像内容、跨会话图片拒绝、未知引用拒绝、输入/图片预算、来源路径校验、幂等失败持久化、pending 恢复及 MySQL 并发序号/图片登记。唯一跳过项是 SQLite 行锁并发测试，对应真实 MySQL 用例已执行。
- `cd frontend && npm run build`：TypeScript 与 Vite 构建通过。
- Chromium `npm run test:ui`：**1 passed**；使用 API 固定响应验证文档选择、图片预览、纯图片问题请求、网络失败后沿用原幂等键、消息刷新及真实 Markdown 图文来源展示；恶意 HTML、事件属性、javascript URL、外部追踪图片均被过滤且没有外部请求。这项浏览器测试不冒充真实模型联调，真实后端联调由上述测试单独完成。
- `git diff --check` 通过。

复验入口（服务需事先独立准备，该测试不启动共享 ES/模型；MySQL 由显式选择的 fixture 启动并清理）：

```bash
cd backend
RAG_TEST_MYSQL_BINARY=/usr/sbin/mysqld \
RAG_LIVE_ES_URL=http://127.0.0.1:19200 \
RAG_LIVE_LLM_URL=http://127.0.0.1:8100/v1 \
RAG_LIVE_ARTIFACT_DIR='<已有 MinerU 产物目录，含 normalized.json 和 images/>' \
/usr/local/miniconda3/envs/py312/bin/python -m pytest -x -q
```

不设置 RAG_TEST_MYSQL_BINARY 时默认跳过隔离 MySQL 用例；不设置三个 RAG_LIVE_* 变量时跳过真实 ES/模型测试。可单独运行 `tests/test_rag_live.py`，它使用已有 basic 产物，不再次运行 MinerU 或 Kafka。

浏览器检查首次需 `cd frontend && npm ci && npx playwright install chromium`，系统需有 Chromium 运行依赖。本环境缺 libgbm/wayland-server，因此只将这两个 .deb 解压至项目忽略目录 `runtime/browser-libs/extracted`，通过对应 `usr/lib/x86_64-linux-gnu` 的 LD_LIBRARY_PATH 执行 `npm run test:ui`，未安装系统包或修改共享配置。

当前边界：检索模式为 BM25，向量/重排未实现，配置启用它们会明确失败；最大 8 图、20MiB 图像、高并发与模型窗口边界仍需压力验证（实测服务上限 4 图，应用须与服务一致）；图片上传孤立文件暂没有自动补偿工具；会话首版逐轮独立检索。文档管理/上传 UI、鉴权、多租户、Kafka 真实传输、advanced/8104、常驻服务与 Nginx 部署不在本轮完成范围。以下条目保留历史状态，较早的“问答待实现”结论以本节覆盖。

## 2026-11-13 MinerU 与 BM25 应用验收

本轮实现并执行 CLI → 结构化产物 → 来源分块 → ES BM25 → MySQL completed，直接调用 Worker，不经过 Kafka。使用项目 MySQL 现有库新增专属测试文档/任务；ES 8.15.3 以 esuser 在独立数据/配置目录 `runtime/bm25-acceptance/es/` 临时运行，仅绑定 127.0.0.1:19200（transport 19300），堆 512MiB。未覆盖共享 ES 配置、未启动共享 9200。验收后本轮临时 ES 已停止，19200/19300 均已确认释放；测试记录、PDF 与解析产物保留在项目忽略目录，未提交 Git。

- 真实成功样例：`/workspace/pdf/test2.pdf`，basic CPU，CLI 与应用处理共 31.49 秒；6 页、11 张图片关联，MySQL 任务 `07b0fea6-005b-45d8-97a9-1ab6824dc688` 为 completed，错误码为空。
- 索引 `multimodal-rag-bm25-acceptance-20261113` 使用 standard 分词、默认 BM25。查询“购物车 100”命中 3 条，首条为第 2 页“Q: 购物车可以添加多少件商品？A:购物车可以添加 100 件商品。”，保留原文件名、块序号、页码与同页图片标识。
- 完整报告：`runtime/bm25-acceptance/report.json`。归一化产物位于 `data/parsed/6fbd7d55-46b1-4133-b03a-eacdff6c94d3/v1/2a80db64-afcf-4a77-9c2a-7473763d7387/normalized.json`。
- 失败验收：使用真实 MySQL、零自动重试预算。实际 CLI 超时（1 秒）任务 `fa2746cb-af3e-4891-8a2b-e4bc992c532d` 保存 MINERU_TIMEOUT/parsing；注入损坏 ZIP 的任务 `55e33416-392b-4c93-88a0-cf86c9a10c64` 保存 INVALID_ZIP/validating；复用真实样本 ZIP，向专属故障索引设置 text=integer 使真实 ES bulk 拒绝写入，任务 `97bc06a3-b9b1-4e89-a01d-eeb6cc153c74` 保存 INDEX_UNAVAILABLE/indexing。三者均为 failed，含 finished_at，未发布 current_searchable_version。故障注入与结果见 `runtime/bm25-acceptance/failures.json`。默认非零预算仍按已有策略有限重试，不会首次暂时失败就终止。
- 自动检查：py312 `cd backend && python -m pytest` 为 **54 passed, 2 skipped**；`cd frontend && npm run build` 通过。临时 MySQL 实例测试未设置 RAG_TEST_MYSQL_BINARY，按约定跳过；本轮应用验收使用实际项目 MySQL，二者不可混同。

复验需先准备可用项目 MySQL、独立 ES 和 MinerU 模型（该脚本不启动服务）：

```bash
PATH=/usr/local/miniconda3/envs/py312/bin:$PATH MINERU_DEVICE_MODE=cpu HF_HUB_OFFLINE=1 OMP_NUM_THREADS=2 MKL_NUM_THREADS=2 \
  /usr/local/miniconda3/envs/py312/bin/python scripts/verify_bm25.py \
  --pdf /workspace/pdf/test2.pdf --query '购物车 100' --tier basic \
  --es-url http://127.0.0.1:19200 --es-index multimodal-rag-bm25-acceptance-20261113 \
  --report runtime/bm25-acceptance/report.json
```

当前服务层支持 BM25 检索及数据库文档范围/版本过滤，HTTP 检索、图片资源和问答 API 仍未实现；advanced/8104、Kafka 真实传输、共享 ES 常驻部署仍待验证。Linux flock 并发实现未覆盖 Windows Worker。下方较早环境状态是历史快照。

## 2026-11-13 服务器复核与真实图片验收

在 `/workspace/hub-iCQD/殷文翔/Week14/作业2` 执行。共享软件、配置、端口和权限只读检查；仅在项目测试目录保存解析样本与报告，临时启动项目专属 8100 模型做图片推理。未启动 MySQL、Kafka、Redis、ES、Nginx，未修改账号、权限、配置、数据库或索引，未安装/升级依赖。以下是最新状态，2026-11-12 的记录保留为历史。

### 环境、连通性和认证

| 检查 | 实际结果 |
| --- | --- |
| 系统 | Ubuntu 22.04.5；内核 6.12.115-compshare；20 核；90GiB 内存；`/workspace` 可用约 23GiB |
| Python/模型软件 | conda py312 Python 3.12.11；MinerU 4.1.7；vLLM 0.25.1；PyTorch 2.11.0（CUDA 13.0）；Transformers 5.14.1 |
| 其他版本 | Node v22.23.5；npm 10.9.10；MySQL 客户端 8.0.46；Redis 可执行程序 7.2.5；Kafka JAR 2.13-4.4.0；ES JAR 8.15.3；Nginx 1.18.0 |
| 连通性 | 本机 TCP 3306、9092、6379、9200、80 均连接失败，`ss` 未见监听；不能据安装文件推定服务运行正常 |
| Kafka 认证配置 | `server.properties` 的业务 listener 为 PLAINTEXT，未配置 authorizer；服务不在线，未验证 broker、Topic、消费组及运行时权限 |
| Redis 认证配置 | `bind 127.0.0.1 -::1`、`protected-mode yes`；未见活动的 requirepass、aclfile 或 user 配置；服务未在线，未做运行时 AUTH/ACL 验证 |
| ES 认证配置 | `xpack.security.enabled=false`、HTTP TLS=false；analysis-ik 目录存在；未验证运行时认证、IK 分词、索引及集群版本 |
| Nginx 认证配置 | 已检查 `/etc/nginx` 配置文件未见活动 basic-auth 指令；80 未监听，未验收反向代理或业务路由 |
| MySQL 认证 | 使用 `.env` 加载项目账号尝试连接，返回 `OperationalError 2003`，3306 不可达；本次没有进入认证阶段，不判定密码是否有效。客户端版本不是本次服务端版本实测 |
| 8001 | 未监听；`127.0.0.1:8001` 临时 bind 成功并立即关闭；本次未启动 API，历史 health/ready 成功不可当作当前可用 |
| 目录权限 | 当前身份 root；`data/{tmp,uploads,parsed,images}`、`logs`、`runtime`、`models`、`mineru` 均为 root:root 755，当前用户可读写；样例 PDF 为 root:root 644。非 root 服务账号写入尚未验证，未改权限 |
| GPU | RTX 4090 49140MiB，驱动 615.78.08；存在 `/dev/nvidia5`、nvidiactl、UVM；`torch.cuda.is_available()`=True，CUDA 初始化与推理成功。`NVIDIA_VISIBLE_DEVICES=void` 仍存在，不能脱离实测将其解释为 CUDA 必然不可用 |
| 依赖一致性 | `python -m pip check` 仍报告 MinerU 4.1.7 要求 `openai<3,>=1.70.0`，实际为 3.48.0；本次未修复依赖冲突，未将成功样例解释为全环境兼容 |

### 可分析的 PDF 解析样本

本次样本保留在 `data/tmp/server-acceptance-20261113T052100Z/`，与旧样本目录分开；该目录受 Git 忽略，仅文档提交，不提交用户 PDF、解析结果、运行日志或权重。

- 输入：`/workspace/pdf/test2.pdf`，6 页，SHA256 `4ef0598c30785d65c96849fa261e25e46b024106a400b3b4a94a9cdf27c4c64c`。
- 执行：py312 的 `mineru-kit parse /workspace/pdf/test2.pdf --format zip -o <本次目录>/test2-basic.zip --tier basic`；子进程设置项目 `MINERU_HOME`、`MINERU_CONFIG`、`MINERU_DEVICE_MODE=cpu`、离线模型读取和 OMP/MKL 各 2 线程。未调用云端解析。
- 结果：退出码 0，耗时 33.24 秒；ZIP 为 1,164,595 字节，SHA256 `4f26462629df5e925f9e949ea8c23605df7c6b7f4e7fe5bdbb1c3ba3a0769f35`；ZIP CRC 检查及项目 `safe_extract` 安全解压通过。
- 文本：`extract/markdown.md` 共 4,747 字符；`structured_content.json` 有 43 个正文/标题块。`middle_json.json` 可读取，顶层含 metadata、extensions、pages、schema 和 schema_version。
- 页码：PDF 原始页数与结构化结果均为 6，`page_idx` 连续为 0–5，用户展示需转换为 1–6 页；正文块、表格和图片均归属对应 page。
- 图片：共 11 张（7 张正文图片、4 张表格裁剪），全部由结构化 JSON 的 `image_source` 关联，均有实际文件，无缺失或孤立图片。7 张正文图片出现在 Markdown；4 张表格以内容呈现，其裁剪图仅在 JSON 关联，不能要求它们都出现在 Markdown 图片语法中。

| `page_idx`（展示页码） | 图片文件名（均位于 `extract/images/`） |
| --- | --- |
| 0（1） | `page_0_image_5.jpg` |
| 1（2） | `page_1_image_4.jpg` |
| 2（3） | `page_2_image_1.jpg`、`page_2_table_3.jpg` |
| 3（4） | `page_3_image_5.jpg`、`page_3_table_2.jpg` |
| 4（5） | `page_4_image_1.jpg`、`page_4_image_6.jpg`、`page_4_table_0.jpg` |
| 5（6） | `page_5_image_0.jpg`、`page_5_table_3.jpg` |

完整核对项保存在同目录 `report.json`，含命令、时长、页码及每张图的关联；`extract/` 保留可直接分析的 Markdown、JSON 和图片。本次验证 basic 档，8104 未监听，未重跑 advanced；历史 advanced 失败不等于本次 GPU 仍不可用。该次独立验证时 Worker 尚未接入来源；本轮已将 JSON 页码、图片关联及块位置写入 BM25 分块，见顶部应用验收。

### vLLM 实际模型 ID 和真实图片结果

- 使用本地 Qwen3.5-4B，在 `127.0.0.1:8100` 临时启动 vLLM 0.25.1；BF16、16K 上下文、GPU 比例 0.32、最多 2 序列、每请求最多 4 图、eager 模式，参数与 [models.md](models.md) 6.1 一致。
- 本轮启动 76.02 秒；`GET /health` 和 `GET /v1/models` 均为 200，实际 ID 为 **`rag-vlm`**、`max_model_len=16384`。测试服务未启用 API key，未验证生产认证方案；原 8000 服务未改动。
- 实际输入为本次 ZIP 解出的 `images/page_0_image_5.jpg`，通过 `image_url` 中的 JPEG data URL 发送原始图像字节；不是只发送图片路径、文件名或 OCR 文本。
- 人工查看原图后，补充询问“中间蓝色方框内是什么文字？图中购物车最多可添加多少件商品？”，问句未提供答案；HTTP 200，模型正确返回“问答技能”和“100件”。实际问题及完整响应保存在 `vision-unbiased.json`，视觉验收以此为准。首个问句误写了“绿色方框”，模型沿用了该描述，因此未将首答的颜色描述用作验收证据。
- 同日此前的 `logs/vllm-startup-check-20261113-recheck.json` 另记录文本 `2+3=5` 及同时输入两张实际 PNG 字节的颜色/形状测试，均为 200；这些是补充证据，本轮 PDF 原图测试独立通过。
- 推理时 GPU 使用 14,382MiB / 49,140MiB；临时 vLLM 退出码 0，8100 端口已释放，GPU 回到 1MiB。未留下常驻服务，未修改 `.env`、`LLM_VISION_ENABLED` 或共享服务。

### 验收结论与交付检查

**本次目标已取得可分析的 basic 解析样本，且主模型 vLLM 的真实图片输入验证通过。** 2026-11-12 的 GPU/主模型视觉失败不再是当前阻塞；视觉验证若后续回归失败，上传和文本检索开发可继续，多模态最终验收仍须暂停。

本次通过的是单组件检查：共享服务均未在线，当前 MySQL 登录不可验证；MinerU 专用 VLM 8104、advanced、Embedding/Reranker、ES 索引、图像资源接口、业务图文问答及共卡并发仍待实现或联调。应用保持 `LLM_VISION_ENABLED=false`、BM25、关闭重排，完整多模态 RAG 尚未最终验收。

交付仅修改文档：核对实测报告、样本文件、文档链接、术语及 `git diff --check`；不为文档修改重跑后端 pytest 或前端构建，历史测试数不作本轮执行结果。

## 2026-11-12 服务器环境实测（Ubuntu）

在服务器项目目录 `/workspace/hub-iCQD/殷文翔/Week14/作业2` 执行只读检查、样例解析和模型权重回退测试。未修改共享服务配置；临时启动的 vLLM 因 CUDA 不可用立即退出，未留下常驻进程。

| 检查 | 实际结果 |
| --- | --- |
| 系统/硬件 | Ubuntu 22.04.5，内核 6.12.115；20 核；内存 90GB；`/workspace` 50GB，可用约 26GB |
| GPU | `nvidia-smi` 显示 RTX 4090、49140MiB、空闲；但容器 `NVIDIA_VISIBLE_DEVICES=void`，仅有 `/dev/nvidia1`，`cuInit` 失败，`torch.cuda.is_available()` 为 False |
| Python/MinerU/vLLM | py312 Python 3.12.11；MinerU 4.1.7；vLLM 0.25.1 |
| Node/npm | Node v22.23.5，npm 10.9.9；`npm run build` 通过 |
| MySQL | 8.0.46，`127.0.0.1:3306` 可连；项目账号认证成功，`multimodal_rag` 已含 7 张业务表 |
| Kafka/Redis/ES/Nginx | 9092/6379/9200/80 均未监听；软件已安装：Kafka 2.13-4.4.0、Redis 7.2.5、ES 8.15.3（含 analysis-ik）、Nginx 1.18.0 |
| 认证配置 | Kafka 为 PLAINTEXT 无认证；Redis 仅绑定本机、protected-mode 开启、未配置 requirepass；ES 关闭 security；MySQL 项目账号可用 |
| 8001 端口 | 未监听；bind 测试成功。临时启动 API 后 `/api/health`、`/api/health/ready`、`/api/openapi.json` 均返回 200，随后已停止，端口释放 |
| 目录权限 | 项目 `data/{tmp,uploads,parsed,images}`、`logs`、`runtime` 均为 root:root 755；root 可写，非 root 服务账号不可写 |
| 后端测试 | `cd backend && python -m pytest -q`：**37 passed, 2 skipped**（跳过项为默认不启用的真实 MySQL fixture） |

### MinerU 样例解析

- 输入 `/workspace/pdf/test2.pdf`（6 页），`--tier basic`，CPU 执行，约 37 秒，ZIP 约 1.2MB。样本已保留在 `data/tmp/mineru-verify/`（ZIP + 解压后的 Markdown/JSON/images）。
- ZIP 含 `markdown.md`、`middle_json.json`、`structured_content.json` 和 11 张图片：7 张正文图片 + 4 张表格裁剪。
- 页码与图片关联：`page_idx` 0–5；正文图片 `page_0_image_5` 到 `page_5_image_0`，表格裁剪 `page_2_table_3` 等；复核确认 11 张图均由 JSON 关联且文件存在，7 张正文图还在 Markdown 引用，4 张表格裁剪仅由 JSON 关联，无缺失文件。
- 文本与表格：中文标题、段落和页码归属正常；4 个表格均输出 HTML/Markdown 内容并带裁剪图。
- `--tier advanced` 失败：无法连接 `http://127.0.0.1:8104`，MinerU 专用 VLM 因 CUDA 不可用无法启动，未生成高级 ZIP。

### vLLM 与视觉能力

- 8000、8100、8102、8103、8104 均未监听，无法查询 `/v1/models`；vLLM 实际 served model ID **未验证**。权重配置为 `Qwen3.5-4B`（`Qwen3_5ForConditionalGeneration`，含 `vision_config` 和 image token），规划别名 `rag-vlm`。
- 因 vLLM 无法启动，改用 transformers（CPU、float32）对同一权重做真实图片输入回退测试：输入 MinerU 裁剪图 `page_0_image_5.jpg`，模型正确回答中间蓝色方框文字“问答技能”，并读出底部“100件”。该结果只证明权重和图像处理可读取真实图片，**不等于** vLLM 端点或 8104 MinerU VLM 已验证。
- 当日多模态最终验收被阻塞；2026-11-13 GPU 与 8100 图片验证已通过，8104 和完整业务链路仍待验证。上传与文本检索开发可继续。

## 2026-11-12 outbox、Kafka 与可恢复 Worker

新增 outbox 投递器、Kafka 生产/消费适配、任务租约与条件更新、有限重试、失败 Topic、崩溃恢复和人工重试新版本。未连接真实 Kafka、MinerU 或 Elasticsearch；Kafka 故障、重复投递、租约过期和旧 Worker 覆盖均通过注入故障的本地测试验证。

| 检查 | 实际结果 |
| --- | --- |
| 后端 pytest（默认 SQLite） | **37 passed, 2 skipped**（跳过项为默认不启用的真实 MySQL fixture） |
| 后端 pytest（临时真实 MySQL 8.0.46） | **39 passed**；在隔离临时实例、随机端口和临时数据目录执行，未连接 `.env` 数据库 |
| Kafka 不可用 | 投递失败后事件保持 pending、记录尝试次数与退避时间；Kafka 恢复后同一事件确认后标记 published，任务不丢 |
| outbox 耗尽 | 请求事件持久化 failed，尚 pending 任务同事务标记 failed，并生成待投递的失败 Topic 事件 |
| 投递崩溃恢复 | 过期 publishing 租约可被新投递器领取；发送成功但标记丢失时允许重发，消费者按任务/版本幂等 |
| 重复投递 | 已完成任务再次投递不调用处理器、不生成新结果；processing 活跃租约返回 busy；提前投递不越过 next_attempt_at |
| Worker 失败与重试 | 可重试错误回到 pending 并复用同一 outbox 事件；预算耗尽后 failed、错误摘要持久化、失败事件入库 |
| 崩溃恢复 | 过期 processing 租约由投递器恢复为 pending；超过重试预算则终态失败，均不依赖 Redis 状态 |
| 旧版本保护 | 人工重试创建新任务/版本；旧租约完成、重试、失败更新均因 latest_version/token 条件失败，不能覆盖新版本 |
| offset | 结果、重试调度或终态失败事务提交后才 commit；模拟 MySQL 提交失败时不 commit 并回退记录重试 |
| 人工重试 API | 相同幂等键返回原任务，不同请求冲突；版本过期 409，新任务与 outbox 同事务提交 |

MinerU CLI 调用、安全 ZIP 解压、分块和 ES 写入适配已实现但未执行真实模型/索引联调；Kafka broker、Topic 权限、消费组、共享文件存储和服务器并发仍待服务器验证。本轮不修改数据库结构。


## 2026-11-12 切换到 conda py312

项目依赖改为使用系统 conda 环境 `py312`（`/usr/local/miniconda3/envs/py312`，Python 3.12.11），不再使用根目录 `.venv`。已在该环境安装 `backend/requirements-dev.txt`，并将依赖清单调整为与同环境 vLLM 兼容的版本。未连接共享 MySQL，未启动 Kafka、MinerU 服务或模型。

| 检查 | 实际结果 |
| --- | --- |
| 项目依赖 | py312 中安装 requirements-dev.txt；SQLAlchemy、Alembic、PyMySQL、pytest 可用 |
| 后端 pytest | `python -m pytest -q`：**25 passed, 2 skipped**（跳过项为默认不启用的真实 MySQL 测试） |
| MinerU / vLLM | `import mineru`、`import vllm` 成功；`mineru-kit --version` 显示 4.1.7，`vllm --version` 显示 0.25.1 |
| pip check | 仍有一条切换前已存在的 `mineru 4.1.7` 要求 `openai<3` 与已装 `openai 3.48.0` 冲突；本次未改动 openai |

为兼容同环境 vLLM 0.25.1，`fastapi`、`uvicorn`、`pydantic-settings`、`python-multipart` 使用其已安装的较新版本，未按旧锁文件的较低版本降级。

## 2026-11-12 模型权重下载

使用 ModelScope 源下载 [models.md](models.md) 要求的权重到项目根目录，因 Hugging Face 经当前代理速度过低（实测约 0.2 MB/s），改用 ModelScope（实测约 5～20 MB/s）。未启动 vLLM、MinerU VLM 或解析服务，未执行真实模型推理。

| 内容 | 结果 |
| --- | --- |
| Qwen3.5-4B | 9.34 GB，15 个文件，文件大小与 ModelScope 元数据一致 |
| Qwen3-VL-Embedding-2B | 4.27 GB，21 个文件，大小一致 |
| Qwen3-VL-Reranker-2B | 4.27 GB，24 个文件，大小一致 |
| MinerU 小模型 + 专用 VLM | `MinerU-4_models_torch` 与 `MinerU2.5-Pro-2605-1.2B` 下载并通过 `models verify` |
| Reranker 模板 | 来自 vLLM v0.25.1，保存到 `model-templates/qwen3_vl_reranker.jinja` |
| 残留临时文件 | 未发现 `.incomplete` / `.part` |

首次 MinerU 下载因缺少 `mineru/config.yaml` 失败；补建配置后下载完成。最终配置使用本地小模型与 `http://127.0.0.1:8104` 的外部 VLM 端点，服务尚未启动。权重体积大，下载期间磁盘可用空间从约 46GB 降至约 26GB。

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

激活 conda 环境 `py312` 后，在 backend 执行：

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

- Kafka 投递/消费、租约恢复和失败 Topic 的服务器联调；basic 解析、索引与 MySQL Worker 流程已通过，Kafka 传输仍待验证。
- MinerU 8104/advanced、共享 ES 常驻部署、Redis、Embedding/Reranker 的运行时联调；主模型真实视觉输入已独立通过，应用图片客户端尚未实现。
- 图片资源接口、会话/消息 API、真实图文问答及完整业务前端；这些路由尚未注册。上传/文档/任务路由已实现。
- 共享 MySQL 恢复后的服务端版本/账号/库与迁移验证；非 root 目录写权限、Nginx 接入和四模型共卡峰值预算。当前端口、文件权限和单模型能力检查见最新记录。

上传大小/数量限制、ZIP 安全解压、重试与租约已实现；JSON 页码/图片来源已入索引；业务图像预算执行仍待完成。单元测试和独立组件通过不代表完整 RAG 系统已实现或服务器联调成功。
