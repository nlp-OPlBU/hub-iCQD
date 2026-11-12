# 基于 Kafka 的多模态 RAG 问答系统（MinerU 解析 + 百炼图文问答）

复刻一套多模态 RAG 问答：**网页上传多个 PDF → Kafka 异步投递 → MinerU 解析入库 → 图文混排问答**，
解析状态（待解析 / 解析中 / 解析完成）实时回推到网页。

三项作业要求与实现位置的对应：

| # | 要求 | 实现 | 代码 |
|---|------|------|------|
| 功能 1 | 前端上传多个 PDF，经 Kafka 到后端，MinerU 解析后存储 | `POST /api/documents` 落盘后投递 topic `mmrag.pdf.upload`；解析 Worker 调 MinerU，产物（文本块 / 表格 / 图片）落盘 + 向量入库 | `app.py` `worker.py::ParseStage/IndexStage` `parsers/` |
| 功能 2 | 前端显示解析状态：待解析、解析中、解析完成 | Worker 把状态发到 `mmrag.doc.status`，Web 订阅后以 **SSE** 推给页面；状态同时落 SQLite，刷新/重启不丢 | `messages.py` `bus.py` `app.py::/api/events` `static/index.html` |
| 功能 3 | 多模态问答：输出图文混排；支持图片 + 问题一起送 LLM | 检索文本块/**图片块**，答案 markdown 内嵌 `/api/images/{id}`；用户图与问题进**同一条**多模态消息送 qwen-vl | `rag.py` `llm.py` `worker.py::QAStage` `static/index.html` |

---

## 架构

```
                        ┌──────────────────────────── Kafka (KRaft, localhost:9092) ───────────────────────────┐
                        │                                                                                      │
浏览器                  │   mmrag.pdf.upload       mmrag.pdf.parsed        mmrag.doc.status        mmrag.qa.*    │
  │  ①多选上传          │        │                       │                        ▲                      ▲         │
  ▼                     │        ▼                       ▼                        │                      │         │
Web(FastAPI) ──produce──┼──▶ ParseWorker ──produce──▶ IndexWorker ──produce──────┤                      │         │
  │  app.py             │    (MinerU 解析)          (图片描述+bge向量化+FAISS)   │                      │         │
  │  ②SSE 状态推送 ◀─────┼─────────────────────── 订阅 status ────────────────────┘                      │         │
  │  ③POST /api/qa ─────┼──────────────────── produce ─────────────────────────────────────▶ QAWorker ───┘         │
  ▼                     │                                                                 (检索+生成，流式)        │
前端 static/index.html ◀─┼────────────────────── 订阅 qa.answer（delta 流）────────────────────────────────────────┘
```

* **三个 Worker 角色**（`parse` / `index` / `qa`）默认跑在 Web 进程内（`WORKERS_INPROC=1`，一条命令即可演示），
  也可以 `WORKERS_INPROC=0` + `python worker.py --roles parse` 拆成独立进程 —— **代码不用改**，这正是 Kafka 解耦的意义。
* 所有消息都用 `doc_id` / `request_id` 作 key，保证同一文档（同一请求）落同一分区、按序处理。

## 目录结构

```
week14/作业2/
├── docker-compose.yml       # Kafka KRaft 单节点（免 ZooKeeper）
├── run.sh                   # 一键：起 Kafka → 起 Web / 冒烟测试 / 停服务
├── config.py                # 全部配置（env > .env > 默认值）+ 配置自检
├── messages.py              # Kafka 消息契约：topic payload、状态机、阶段与进度
├── bus.py                   # Kafka 封装：produce / 每 topic 一个消费线程 / 建 topic / 健康探测
├── store.py                 # SQLite（文档·状态·块·图片·问答历史）+ FAISS 向量索引
├── chunking.py              # 语义块 → 检索单元（文本滑窗 / 表格整块 / 一图一块，标题作为上下文）
├── embed.py                 # 百炼 text-embedding-v4（默认）/ 本地 bge-small-zh-v1.5 / mock
├── llm.py                   # 阿里百炼（OpenAI 兼容模式）：图+问同一条消息、流式、图片描述
├── rag.py                   # 多模态检索 + 上下文组装 + 图文混排答案生成
├── parsers/
│   ├── __init__.py          # 调度：MinerU 在线则用 MinerU，否则内置解析器
│   ├── mineru.py            # MinerU HTTP 适配（同步 /file_parse + 异步 /tasks 带真实进度）
│   ├── local_pdf.py         # 内置兜底：pdfplumber 文本/表格 + pypdfium2 渲染图片区域
│   └── common.py            # ParsedDoc 统一结构 + 折行拼接（中文/英文/名字类 token）
├── worker.py                # 三个 Kafka 消费角色（parse / index / qa）
├── app.py                   # FastAPI：上传、状态 SSE、问答 SSE、图片、健康检查、链路可视化
├── static/index.html        # 前端单页（零依赖零构建）：上传、状态列表、图文混排问答
├── scripts/
│   ├── gen_sample_pdf.py    # 生成带图表的示例 PDF
│   └── smoke.py             # 端到端冒烟测试（真起服务，断言三个功能）
├── requirements.txt
├── .env.example
└── samples/                 # 示例 PDF（由脚本生成，已在 .gitignore 中）
```

## 快速开始

```bash
conda activate py312
cd 盛杰厚/week14/作业2

# 1) 依赖
pip install -r requirements.txt

# 2) 配置（唯一必填项是百炼 Key；不填也能跑通全链路，回答走 mock）
cp .env.example .env        # 编辑 .env，填 DASHSCOPE_API_KEY

# 3) 起 Kafka（Docker Desktop 需已启动）
docker compose up -d
docker compose ps           # 等到 mmrag-kafka 变成 healthy

# 4) 起 Web（默认 8010，进程内自带三个 Worker）
./run.sh                    # 等价于 python -m uvicorn app:app --port 8010
# 浏览器打开 http://localhost:8010
```

页面操作：**①拖入多个 PDF** → **②看状态从「待解析→解析中（带进度与阶段）→解析完成」**
→ **③提问**（可以点「加图片」或直接粘贴截图）→ 答案里会内嵌文档中的配图。

### 没有 MinerU 也能跑

`parsers/__init__.py` 会先探测 MinerU：

```bash
pip install -U "mineru[core]"      # 装好之后
mineru-api --host 127.0.0.1 --port 8002
```

* MinerU 在线 → 用它（版面/表格/公式还原更好），页面状态里 `parser=mineru`；
* 不在线 → 自动用**内置解析器**（pdfplumber 取文本行与图片框，pypdfium2 把图片框渲染成 PNG），
  状态里如实显示 `parser=pdf_local`；
* 想强制要求 MinerU：`.env` 里 `MINERU_STRICT=1`，不可用即判「解析失败」。

两条路产出**同一种中间结构** `ParsedDoc`，下游（切块/向量化/问答）完全无感。

### 没有 API Key 也能跑完整链路

`DASHSCOPE_API_KEY` 为空时自动进 **mock 模式**：回答由检索片段机械拼装，但**仍然带图片 markdown**，
所以「Kafka → 解析 → 向量化 → 检索 → 图文混排渲染」都能验证；页面顶部会明确标注 mock。
配上百炼 Key 后即为真实模型回答（`/api/health` 里 `llm.mock=false`）。

### 端到端自测

```bash
python scripts/smoke.py          # 自动起临时服务、跑三个功能、断言，最后清理
python scripts/smoke.py --keep   # 跑完保留服务方便手点

# 机器忙 / 只想快速验证链路（不加载 torch，用哈希向量）：检索质量不作数
python scripts/smoke.py --mock-embed --startup-timeout 150 --timeout 150
```

本机实测（`--mock-embed`，19 项全过）的关键证据：

```
✅ Kafka 后端可用 —— backend=kafka localhost:9092
✅ 多文件上传 0.8s 返回（只落盘 + 投递，不等解析）
✅ 两份相同 PDF：1 份入库 + 1 份识别重复
✅ 状态 pending → parsing → done
   细粒度阶段：queued, mineru, mineru_fallback, images, chunking, embedding, indexing, indexed
✅ chunks=15 images=3 parser=pdf_local pages=3
✅ 检索命中文本块 + 图片块（top score 0.551）        ← 图片确实进了向量空间
✅ 回答里内嵌 ![](/api/images/1)，该 URL 可下载 52KB   ← 图文混排
✅ 带图提问：images_sent_to_model=2（用户图 1 + 文档图 1）← 图问一起送模型
✅ 消费组 mmrag-parser / mmrag-indexer / mmrag-qa 均已注册，收发 49 条无异常
```

**接了百炼 Key 之后的实测（21/21 全过，真实模型）：**

```
✅ Embedding 非 mock —— text-embedding-v4（1024 维，分批 ≤10 条/请求）
✅ 解析完成：15 个检索单元 / 3 张配图，图片描述由视觉模型生成
✅ 检索命中 top score 0.668
✅ 回答流式返回 624 段增量
✅ 答案内嵌 /api/images/1、/api/images/2 两张图，URL 可下载   ← 真实图文混排
✅ 带图提问：images_sent_to_model=2（用户图 1 + 文档图 1），模型 qwen3-vl-plus
```

> 本机内存紧张时，可以不用本地 torch 跑 embedding（见下节 `EMBED_BACKEND=openai`），
> 其余链路完全一致；上表就是这么跑出来的。

> 说明：测试时本机没有 `DASHSCOPE_API_KEY`，所以 ⑤⑥ 两步的回答来自 mock（检索片段拼装），
> 但**图片内嵌、URL 可访问、多模态消息组装**这些链路环节都是真跑的。
> 配上 Key 后同一命令会走 qwen3-vl-plus（或账号实际开通的多模态模型） 真实生成；`--mock-embed` 只影响向量质量，不影响链路。

## Kafka 设计

| topic | 生产者 | 消费者（group） | 内容 |
|-------|--------|------------------|------|
| `mmrag.pdf.upload` | Web | 解析 Worker（`mmrag-parser`） | 待解析任务：`doc_id`、文件路径、sha256 |
| `mmrag.pdf.parsed` | 解析 Worker | 入库 Worker（`mmrag-indexer`） | 解析产物目录 + 统计（大文本不入消息体） |
| `mmrag.doc.status` | 两个 Worker | Web（`mmrag-week14-web-status`） | 状态、阶段、进度、消息（功能 2 的数据源） |
| `mmrag.qa.request` | Web | 问答 Worker（`mmrag-qa`） | 问题 + 用户图片路径 + 检索范围 |
| `mmrag.qa.answer` | 问答 Worker | Web（`mmrag-week14-web-qa`） | 阶段 / 增量 delta / 最终答案 + 引用 |

几个刻意的选择：

* **大对象不过消息体**：解析出的正文与图片落盘，消息只带目录路径，避免 `message.max.bytes` 限制；
* **key 保证顺序**：同 `doc_id`/`request_id` 的消息进同一分区，状态不会倒着走
  （`store.apply_status` 还有一道单调性保护，防重放/迟到的低优先级状态覆盖结果）；
* **状态双写**：Worker 既发 Kafka 也写 SQLite —— 即使 Web 没在跑，数据库里的状态也是对的；
  页面刷新走 `/api/documents` 快照，不依赖消息回放。

状态机：

```
 pending(待解析) ──Worker 取到任务──▶ parsing(解析中) ──入库完成──▶ done(解析完成)
                                        │ 细分阶段：MinerU 解析 / 抽取图片 / 切分 /
                                        │           生成图片描述 / 向量化 / 写入索引
                                        └──异常──▶ failed(解析失败) + 错误信息
```

## 多模态是怎么做的

Embedding 默认用百炼的 **text-embedding-v4**（1024 维）；也可以切成本地 **BAAI/bge-small-zh-v1.5**（512 维）。两者都是**纯文本**向量模型，图片没法直接进向量空间。
所以这里做了两件事：

1. **图片文字化**（入库侧）：MinerU 抽出的图，优先用解析出的题注（「图 3 …」）；
   题注缺失时调视觉模型生成一句描述。把「`[图片] 第 N 页 + 描述`」向量化，
   与文本块放进**同一个 FAISS 索引** —— 于是问「架构图长什么样」能把图片块召回来。
2. **图文混排输出**（回答侧）：把召回图片以 `/api/images/{id}` 列给模型，要求它在答案里
   **原样插入** `![](/api/images/{id})`；前端 markdown 渲染成真正的图片嵌在文字之间。
   `rag.normalize_image_links()` 会纠正模型写错的图片路径、删掉它虚构的图。

**图问一起送 LLM**：`POST /api/qa` 的 multipart 里带 `files` 图片，
`llm.build_messages()` 把「检索上下文文本 + 用户问题 + 用户图片 + 文档配图」
放进**同一个 user 消息的 content 数组**：

```python
{"role": "user", "content": [
    {"type": "text", "text": "<检索上下文>\n<问题>"},
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},   # 用户图
    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}},   # 检索到的文档图
]}
```

（`llm._prep_image()` 会处理百炼对图片的硬要求：边长 > 28px、过大的图等比缩小、透明通道转 RGB。）

另外，用户图会先被视觉模型读出一句话描述，拼进**检索 query** —— 这样「这张图对应文档哪一节」
才能检索到正确的段落。

### 用远程 embedding 代替本地 torch（内存紧张 / 没装 sentence-transformers 时）

`bge-small-zh-v1.5` 走本地推理要加载 torch（约 0.5GB 常驻）。机器内存不够时，可以换成
百炼的文本向量模型，链路与效果结构完全一致，只是向量模型不同：

```bash
export EMBED_BACKEND=openai
export EMBED_OPENAI_MODEL=text-embedding-v4          # 1024 维；v3 也可以
export EMBED_OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
export EMBED_OPENAI_API_KEY=$DASHSCOPE_API_KEY
python scripts/smoke.py
```

> DashScope 的 `text-embedding-*` **单批上限 10 条**，代码里已按 `EMBED_OPENAI_BATCH`
> （默认 10）分批，超过会直接 400。换 embedding 模型后旧索引维度不兼容，
> 记得删掉 `data/index/` 重新入库。

## 配置速查

完整项见 `config.py` 顶部文档字符串或 `.env.example`。

| 变量 | 默认 | 说明 |
|------|------|------|
| `DASHSCOPE_API_KEY` | 空 | 阿里百炼 Key；空则问答走 mock |
| `LLM_MODEL` / `LLM_TEXT_MODEL` | `qwen3-vl-plus` / `qwen-plus` | 多模态 / 纯文本（模型 ID 以账号实际开通为准）|
| `EMBED_BACKEND` | `openai` | `openai`（百炼 text-embedding-v4）/ `local`（本地 bge）/ `mock` |
| `EMBED_OPENAI_MODEL` | `text-embedding-v4` | 1024 维；单批 ≤10（自动分批）|
| `EMBED_MODEL` | `BAAI/bge-small-zh-v1.5` | 仅 `local` 后端使用，512 维，可离线 |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | |
| `USE_KAFKA` | `1` | `0` 用进程内队列（只为无 broker 时调试） |
| `ALLOW_LOCAL_FALLBACK` | `1` | Kafka 连不上是否降级；`/api/health` 如实显示原因 |
| `MINERU_BASE_URL` | `http://127.0.0.1:8002` | |
| `MINERU_STRICT` | `0` | 1 = 没有 MinerU 就判失败，不降级 |
| `RETRIEVAL_TOP_K` / `RETRIEVAL_MIN_SCORE` | `8` / `0.30` | 召回条数 / 余弦阈值 |
| `CHUNK_SIZE` / `CHUNK_OVERLAP` | `600` / `80` | |
| `WORKERS_INPROC` | `1` | 0 = 用 `python worker.py` 独立进程 |
| `PORT` | `8010` | |

## 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/documents` | 多文件上传（multipart `files`），返回 accepted/skipped/failed |
| GET | `/api/documents` | 文档列表 + 各状态计数 |
| GET | `/api/documents/{id}` | 详情：检索块、图片清单 |
| GET | `/api/documents/{id}/markdown` | 解析出的整篇 markdown（排查解析质量） |
| POST | `/api/documents/{id}/reparse` | 重新投递解析 |
| DELETE | `/api/documents/{id}` | 删除文档（连带向量与图片） |
| GET | `/api/events` | **SSE**：状态快照 + 实时状态（待解析/解析中/解析完成） |
| POST | `/api/qa` | **SSE**：提问（可带 `files` 图片），事件 `accepted → stage → delta* → answer` |
| GET | `/api/qa/history?session_id=` | 多轮历史 |
| GET | `/api/images/{id}` | 解析抽出的图片（答案图文混排的图源） |
| GET | `/api/search?q=` | 只看检索结果（调阈值/换模型时用） |
| GET | `/api/health` | Kafka 后端、topic、消费组、MinerU、embedding、LLM 的真实状态 |
| GET | `/api/pipeline` | 最近状态消息 + 总线指标 + 消费组（答辩演示链路用） |

## 常见问题

**Q：页面顶部提示「Kafka 降级为进程内队列」？**
`docker compose up -d` 起 broker，等 `docker compose ps` 显示 healthy 后刷新页面。
`/api/health` → `bus.fallback_reason` 会写明具体原因（连接失败/配置错误）。

**Q：上传后一直「待解析」？**
说明消息发出去了但没人消费：确认 Worker 在跑（进程内模式看 `/api/pipeline` 的消费组），
或者单独启动 `python worker.py --roles parse,index,qa`。Kafka 的消费位点按 group 记录，重启不会丢任务。

**Q：能搜到文字，但图片搜不到？**
图片是否可检索取决于它的**描述文字**：有题注就直接用；没有题注需要配 `DASHSCOPE_API_KEY`
让视觉模型生成（`IMAGE_CAPTION=1`）。mock 模式下不会调视觉模型，所以图片块只有位置信息。

**Q：MinerU 解析很慢 / 内存高？**
`MINERU_BACKEND=pipeline` 最省资源；扫描件才需要 `vlm-engine` / `hybrid-auto-engine`。
纯文字 PDF 用内置解析器通常更快（`PREFERRED_PARSER=pdf_local`）。

**Q：机器风扇狂转、请求变慢？**
本地 embedding 会拉起 torch。本仓库已把 torch/FAISS 的线程数限制为 2（`EMBED_THREADS`），
若并发仍然高，把它降到 1。

**Q：提示端口被占用 / 服务起来了却连不上？**
多半是上一次被 Ctrl-C 打断的 uvicorn 变成了孤儿进程还占着端口和 CPU
（实测累积 10 个未退出的实例能把 4 核机器压到 load average 200+，新服务连启动都跑不完）。
清理后再起：

```bash
pkill -f "uvicorn app:app"     # 或按端口找：lsof -nP -iTCP:8010 -sTCP:LISTEN
python scripts/smoke.py        # 会自动避开被占用的端口，但清理后测试才快
```

## 踩坑记录（kafka-python 2.0.3 / sentence-transformers 6 的 API 差异）

这些坑都会**静默降级**、看起来像「消息丢了」或「服务卡死」，所以写在这里：

1. `KafkaAdminClient.list_topics()` **不接受** `timeout_ms` 参数 → 传了会 TypeError，
   探测逻辑一 catch 就永远返回 False，服务悄悄切成进程内队列。
2. `KafkaAdminClient` **不认识** `connection_timeout_ms`（会抛 `KafkaConfigurationError`），
   超时只能配 `request_timeout_ms`。
3. `ConsumerRecord` **没有** `.error` 属性 → 消费循环里访问它会让线程崩溃并无限重连，
   表现是「消息一条都收不到」。
4. `SentenceTransformer.encode()` 在 6.x **不再接受** `truncate_tokens` 之类多余 kwarg
   （抛 ValueError → embedding 后端降级成 mock），截断改由 `max_seq_length` 控制。
5. `async def` 端点里做同步阻塞调用（如健康检查里加载模型、建 Kafka admin client）
   会**阻塞整个事件循环**，所有请求一起卡死。本仓库把这类端点改成普通 `def`（FastAPI 自动放进线程池），
   并让 `/api/health` 只读缓存、绝不触发模型加载。
6. macOS 上 torch 与 faiss-cpu **各自链接了一份 libomp**，同一个进程里加载第二份会报
   `OMP: Error #15: Initializing libomp.dylib, but found libomp.dylib already initialized`
   并直接 abort —— 表现是「服务启动后无声消失、端口连不上」。
   `config.py` 在导入任何科学计算库之前设置了 `KMP_DUPLICATE_LIB_OK=TRUE`（唯一官方绕过方式），
   同时把 `OMP_NUM_THREADS` / `MKL_NUM_THREADS` 压到 2（见第 7 条）。
7. torch/OpenMP 默认按 CPU 核数开线程；本服务一个进程里还有 5 个 Kafka 消费线程，
   多进程（Web + 独立 Worker）一起跑时实测能把 4 核机器压到 load average 上百，
   所有请求排队超时。用 `EMBED_THREADS`（默认 2）限制线程数。

## 已知限制

* 解析器兜底方案对**扫描件**（整页是图、没有文字层）无能为力，需要 MinerU 的 OCR 后端；
* 图片检索本质是「描述文本检索」，描述质量直接决定召回；换真正的多模态 embedding
  （如 DashScope `multimodal-embedding-v1`）可以把图片向量直接放进索引 —— `embed.py` 已预留后端切换；
* 单机演示用 SQLite + FAISS 单索引，没做多副本/分片；生产应换 Milvus/pgvector 等。
