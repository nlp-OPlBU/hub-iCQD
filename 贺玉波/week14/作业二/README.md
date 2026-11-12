# Week14 多模态 RAG

PDF 上传 → Kafka 异步解析（MinerU）→ 分块向量化（文本 + 图像）→ LangGraph 图文问答 → 全流程节点延迟监控。

当前进度：**P1–P10 完成并服务器端到端验证**（真实 PDF 上传 → Kafka → MinerU flash 解析 → CLIP/Embedding 入库 → 检索 → DeepSeek Flash 图文问答）。

## 架构

```
浏览器 ──▶ FastAPI(8080) ──▶ MySQL(3306)    文档状态唯一真源
                │            Redis(6379)    状态投影 / 会话历史 / 幂等 / trace 缓存
                │            Kafka(9092)    doc.ingest 单分区串行队列
                │            Milvus Lite    独立服务 :19530，doc_text(1024d) + doc_image(512d)
                │            对话模型       本地 vLLM(8000) 或云端 OpenAI 兼容 API
                ▼
        ingest-worker ──▶ MinerU flash → chunk → embed → Milvus → MySQL
```

分层：`api → services → domain`，`services → infra`；`pipeline` 承载分块与 LangGraph 图。
依赖方向单向，接口层不写业务，业务层不碰驱动。

## 目录

```
backend/app/core       配置、JSON 日志、span 采集、异常、lifespan
backend/app/domain     枚举、状态机（纯函数，可 100% 单测）
backend/app/infra      MySQL / Redis / Kafka / Milvus / vLLM / 向量模型（embedding、CLIP）
backend/app/services   业务编排（文档入库、检索）
backend/app/pipeline   MinerU 解析、切块、向量索引（纯函数为主）
backend/app/worker     ingest-worker 消费循环、reindex 离线重建
backend/app/graph      LangGraph 问答编排（路由 → 检索 → 上下文）
backend/app/api        HTTP 适配、统一错误体、探针
backend/sql            DDL 唯一真源（schema_version 记录已应用脚本）
backend/tests          unit（无 I/O）+ integration（真实中间件）
frontend               React 18 + Vite + TS（上传 / 文档 / 问答 / 追踪四页）
deploy                 supervisord、redis.conf、kafka server.properties、启停脚本
scripts                sync.ps1（本地同步）、smoke.sh
```

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents` | 上传 PDF，落盘 + 入库 `pending` + 投递 Kafka；返回 202（重复文件返回 200 且不重复入队） |
| GET | `/api/v1/documents?limit=&offset=&status=` | 文档列表（按创建时间倒序） |
| GET | `/api/v1/documents/{id}` | 文档详情（状态、进度、块数、错误码） |
| GET | `/api/v1/documents/{id}/chunks?limit=&offset=` | 已切分文本块 |
| POST | `/api/v1/documents/{id}/retry` | `failed` 重新投递（非 failed 返回 409） |
| POST | `/api/v1/search` | 检索：`{query, top_k, with_images, doc_id}`，文本块 + 图片双路召回后 RRF 融合 |
| GET | `/api/v1/search?q=&top_k=&with_images=` | 同上，便于 curl / 浏览器直接演示 |
| POST | `/api/v1/chat` | 问答：`{question, session_id?, top_k?, doc_id?, stream?}`；`stream=true` 返回 SSE（`meta` → `delta`* → `done`） |
| GET | `/api/v1/chat/{session_id}/history` | 会话历史（MySQL 为准，Redis 缓存最近 20 轮） |
| DELETE | `/api/v1/chat/{session_id}` | 清空会话 |
| GET | `/api/v1/traces/{trace_id}` | 按 `X-Request-Id` 还原 span 瀑布 + 节点延迟排行（先读 Redis，过期回落 MySQL） |
| GET | `/metrics` | Prometheus 文本格式：HTTP 计数/延迟、各 span 直方图、Kafka 消费 lag |
| GET | `/health` / `/ready` | 存活 / 依赖就绪 |

## 服务器启动

```bash
cd /workspace/week14
cp .env.example .env        # 填 MYSQL__PASSWORD
make bootstrap              # 装依赖 + 校验配置
make up                     # 中间件 → Milvus Lite → 建库建 topic → (vLLM) → api，等待 /ready
```

`make up` 只在 `/ready` 五项（MySQL / Redis / Kafka / Milvus / vLLM）全绿时返回 0。
Milvus Lite 由 supervisord 以独立进程跑在 `127.0.0.1:19530`：本地 `.db` 文件模式单进程独占，
api 与 worker 必须连同一个服务才能共享向量库（`.env` 里 `MILVUS__URI=http://127.0.0.1:19530`）。
只调试接口时用 `make up-core`（跳过 vLLM，`/ready` 会显示 vllm 未就绪）。

模型权重建议用魔搭预下载到本地目录，切换 GPU 实例时不必重新下载：

```bash
python -c "from modelscope.hub.snapshot_download import snapshot_download as d; \
d('Qwen/Qwen3-Embedding-0.6B', local_dir='models/Qwen3-Embedding-0.6B'); \
d('AI-ModelScope/chinese-clip-vit-base-patch16', local_dir='models/chinese-clip-vit-base-patch16')"
```

然后在 `.env` 里把 `EMBED__MODEL` / `CLIP__MODEL` 指向这两个目录（绝对路径）。
模型目录在 `models/` 下，属于运行时数据，不进版本库。

常用命令：

| 命令 | 作用 |
|---|---|
| `make status` / `make logs` | 进程状态 / 跟踪 api + worker 日志 |
| `make down` | 停止全部受管进程 |
| `make demo-redis` / `make demo-kafka` | 作业1：Redis 会话缓存 / Kafka 重试与 DLQ 演示 |
| `make test` | 单测 + 集成测试（真实 MySQL/Redis/Kafka/Milvus，独立 `rag_test` 库与 `doc.ingest.test` topic） |
| `make test-model` | 真实向量模型冒烟（首次会下载权重，需 GPU 显存） |
| `make reindex` | 不重新解析，直接用库里的分块重建向量（换模型 / 集合重建时用） |
| `make eval` | 按 `docs/eval/golden.jsonl` 评检索质量（hit@k / recall@k / MRR / nDCG@k + 延迟 p50/p95） |
| `make smoke` | 探针 + 依赖耗时打印 |
| `make api-schema` | 导出 `docs/openapi.json` 契约快照 |
| `make frontend-install` / `make frontend-build` | 前端依赖安装 / 构建（产物 `frontend/dist` 由 FastAPI 托管） |

## 对话模型：本地 vLLM / 云端 API

生成与摘要都走同一个 OpenAI 兼容客户端（`app/infra/model/vllm_client.py`），切换只改 `.env`，代码不变：

```bash
# 本地（默认，作业要求的部署形态、离线可用）
VLLM__PROVIDER=local
VLLM__BASE_URL=http://127.0.0.1:8000/v1
VLLM__MODEL=qwen2.5-vl-7b

# 云端（更快更强、不占显存，适合演示与质量对比）
VLLM__PROVIDER=cloud
VLLM__BASE_URL=https://api.deepseek.com/v1
VLLM__MODEL=deepseek-flash
VLLM__API_KEY=sk-xxx
```

- `provider=cloud` 时 `make up` 自动跳过 vLLM，省下约 20GB 显存；`/ready` 的 `vllm` 项改为真实探测云端 API（超时放宽到 5s）。
- 云端模式下缺 `VLLM__API_KEY` 会启动即失败，避免跑起来才发现 401 全挂。
- 检索（Milvus）、解析（MinerU）、向量模型（Embedding / CLIP）仍在本地，图片与文档不出域；只有「问题 + 命中文本 + 命中图片」发往云端。
- 命中图片会编码成 `data:` URL 作为 `image_url` 内容块真正交给模型（最多 `VLLM__MAX_IMAGES` 张、单张 ≤4MB）；每张图前面插一条 `【图N｜《文档》第X页】` 标签，问题放最后，保证图文严格对齐，而不是把图全堆在末尾。设为 0 则退回纯文本。
- 问答输入框支持直接粘贴图片（或点「图片」选择文件），走 `data:image/...` base64 随问题一起发送，此时只按用户图作答、不混入知识库资料。

## 前端

React 18 + Vite + TS，六个页面：

| 页面 | 内容 |
|---|---|
| 上传文档 | 拖拽/点选 PDF，校验与异步入队提示 |
| 文档列表 | 状态徽章 + 进度条，3s 轮询到终态停止，失败可重投；点文件名进详情 |
| 文档详情 | 基本信息、失败原因、分块列表（页码、文本、图片预览、可复制 chunk_id，用于标注评测集） |
| 检索调试 | 直连 `/api/v1/search`，调 top_k、开关图片通道，看 score / 来源通道 / chunk_id |
| 智能问答 | 流式对话、引用与图片缩略图、session 续聊 |
| 链路追踪 | trace_id 查 span 瀑布与节点延迟排行 |
| 指标监控 | 解析 `/metrics`，概览 + 计数器 / gauge / 直方图表 |

```bash
# 本地开发（/api、/static、/metrics 代理到远端 8080，需先建 SSH 隧道）
ssh -N -L 8080:127.0.0.1:8080 -p 22057 root@<pod 域名>
cd frontend && npm install && npm run dev     # http://localhost:5173

# 交付：本地构建后只同步 dist，云端不装 node_modules
npm run build
```

`frontend/dist` 存在时，FastAPI 会把 `/` 指向前端，`/api` 与 `/static` 保持原路径。

## 本地同步

源码真源在本机 `A:\week14`，服务器 `/workspace/week14` 只作运行副本：

```powershell
.\scripts\sync.ps1
```

打包时排除 `data/ logs/ __pycache__ .env node_modules`，服务器上的 `.env` 不会被覆盖。

## 评测与观测

### 检索质量评测

RAG 先用检索质量验收，再谈生成效果：召回不对，模型只能编。`app/eval` 只评测检索这一段，不调生成模型（生成打分成本高且主观，放在人工抽检）。

标注文件 `docs/eval/golden.jsonl`，一行一个样本（`#` 开头为注释）：

```json
{"query": "多模态检索是怎么做的？", "relevant": ["<chunk_id>", "<chunk_id>"]}
{"query": "图表里的指标说明了什么？", "relevant": ["<image_chunk_id>"], "doc_id": "<可选，限定单篇>"}
```

```bash
# 先在「文档」页上传并等到 done，再从 /api/v1/documents/{id}/chunks 取 chunk_id 标注
make eval                                  # 默认 top_k=8，写 docs/eval/report.json
$PY -m app.eval.run --with-images --min-recall 0.6   # 带图片通道；recall 低于阈值以非 0 退出（可接 CI）
```

指标口径（`app/eval/metrics.py`，纯函数、可离线复算）：

| 指标 | 回答的问题 |
|---|---|
| hit@k | top-k 里能不能找到至少一个正确块 |
| recall@k | 正确块找全了没有（命中数 / 标注总数） |
| MRR | 第一个正确块排得靠不靠前 |
| nDCG@k | 排名质量，比 recall 更惩罚"正确答案排在后面" |
| 延迟 p50 / p95 | 最近秩法百分位，长尾更能反映用户体感 |

报告里同时记录每条 query 的 `retrieved` 与 `miss`（漏掉的 chunk_id），换模型 / 改分块 / 调 RRF 参数后可直接 diff 两份 `report.json` 判断是否退化。

### 运行指标

`/metrics` 输出 Prometheus exposition 文本，无需额外 exporter：

| 指标 | 类型 | 说明 |
|---|---|---|
| `rag_http_requests_total{method,path,status}` | counter | 按**路由模板**打标签（不是真实路径），避免 session_id 打爆基数 |
| `rag_http_duration_ms{method,path,status}` | histogram | 请求耗时，桶 5ms~30s |
| `rag_span_duration_ms{span,status}` | histogram | 各节点（`search.embed` / `search.knn` / `qa.generate` …）耗时 |
| `rag_kafka_consumer_lag` | gauge | worker 每轮 poll 后探测的消费 lag |

指标在进程内维护（`app/core/metrics.py`，threading.Lock 保护），重启清零；单实例部署下比引入 prometheus_client 更直接，接口语义与 Prometheus 对齐，需要多实例聚合时再换 exporter。

可靠性细节：vLLM 非流式调用只对可自愈错误（网络抖动 / 5xx / 429）指数退避重试，4xx 直接失败；流式不重试（已下发的 token 撤回不了）。SSE 客户端中途断流时，已生成的部分回答会交给后台任务落库，会话里不会只剩问题。

## 工程约定

- 日志只有 JSON 单行：`ts/level/logger/trace_id/event/msg` + 业务字段；禁止 `print`。
- 每个 HTTP 请求生成 `trace_id`，响应头回写 `X-Request-Id`；Kafka 消息透传同一 `trace_id`，"上传 → 解析完成" 可串成一条链路。
- 状态写入必须走状态机（`pending → parsing → chunking → indexing → done`，失败进 `failed` 后可重试），非法迁移返回 `409 CONFLICT_STATE`。
- Redis 只是投影，任何状态都能从 MySQL 重建；写库成功后再写缓存。
- 会话真源是 `chat_session` / `chat_message`：先落库再刷 Redis 缓存，缓存随时可清空、可从数据库回填。
- 上下文按 **token 预算**（`CHAT__HISTORY_TOKEN_BUDGET`）成对回溯历史，不按固定条数裁剪；未摘要部分超过阈值后由后台任务滚动摘要写入 `chat_session.summary`，摘要失败退化为纯截断，不影响本轮回答。
- 向量：文本用 `Qwen3-Embedding-0.6B`（1024 维）写 `doc_text`，图片用 `Chinese-CLIP ViT-B/16`（512 维）写 `doc_image`；检索用 RRF 只融合名次，避免不同量纲的相似度直接相加。模型懒加载，CUDA 不可用时自动降级 CPU。
- 引用页码：切块优先用 MinerU 的 `*_content_list.json`；`--tier flash` 只输出单个 Markdown，则按 `<!-- page N of M -->` 标记切页，chunk 仍带真实页码；两者都没有时退回整篇切块、页码留空（前端显示 `?`）。
- MinerU 档位：默认 `--tier flash`（实测 14 页约 6 秒，不依赖 parse-server）；`standard/advanced` 需要 `mineru server`（内部起 vLLM，显存 ≥ 8GB），环境不稳时别开。flash 不落地图片文件，入库前用 PyMuPDF 按页抽原图（过滤 64px 以下的小图标）再交给 CLIP。
- 图片路径只有一套：入库时写 `/static/images/<doc_id>/<name>`，Milvus、上下文、前端 `<img src>` 全部直接使用，不再二次拼接前缀。
- 追踪采样：`TRACE__ENABLED` / `TRACE__SAMPLE_RATE` 在 `TraceSession` 创建时一次性决策；未命中采样时仍计时并上报慢节点日志，但不落库。
- 凭据只从 `.env` 读取，缺失时进程 fail fast。

## 里程碑

| 阶段 | 内容 | 状态 |
|---|---|---|
| P1 | 骨架：配置/日志/追踪/探针/Makefile/supervisord | 完成（`/ready` 五项全绿，20 项测试通过） |
| P2 | 作业1：`demo/redis_chat.py`、`demo/kafka_pipe.py` | 完成（服务器实测：缓存 MISS 605ms → HIT 0.1ms；Kafka 重试 2 次后进 DLQ，lag=0） |
| P3 | 入库链路：上传 → Kafka → worker → MinerU → 状态机 → DLQ | 完成（真实 PDF：pending → parsing → indexing → done，16 text + 10 image） |
| P4 | 分块 + 双集合向量化 + 检索（Qwen3-Embedding + Chinese-CLIP + RRF） | 完成（文本 + 图片双路命中，图片带 `image_path`） |
| P5 | LangGraph 问答 + vLLM + 前端四页 | 完成（云端 DeepSeek Flash 图文问答、SSE 流式均验证） |
| P6 | trace 查询接口 + 节点延迟排行 + OpenAPI 快照 | 完成（集成用例通过） |
| P7 | 会话持久化：MySQL 真源 + Redis 热缓存、token 预算上下文、滚动摘要、并发 seq 分配 | 完成（单测 46 + 会话/链路集成 16 通过，含 8 线程并发用例） |
| P8 | 评测与观测：`/metrics`、span 直方图、Kafka lag、检索质量评测、vLLM 重试、断流落库 | 完成（单测 65 通过；metrics / 断流集成用例通过），golden 标注待真实文档 |
| P9 | 对话模型双后端：本地 vLLM / 云端 DeepSeek Flash，命中图片以 `image_url` 真喂给模型 | 完成（DeepSeek Flash 实测返回图文答案，流式/非流式均通过） |
| P10 | 前端补齐：检索调试台、文档详情（chunk_id 标注）、指标监控页、会话恢复、多 PDF 批量上传 | 完成（构建通过，页面冒烟测试通过） |
