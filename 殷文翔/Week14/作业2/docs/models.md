# 全本地多模态 RAG 模型选型与部署

记录日期：2026-11-11；2026-11-13 更新：项目与模型依赖统一使用系统 conda 环境 `py312`。目标是非垂类、通用图文 RAG。已实测服务器为 CPU 20 核、单张 RTX 4090 49140MiB、90GiB 内存，驱动 615.78.08，CUDA 推理可用。

本文是模型部署指南，当前项目独立部署见下一段。以下先保留单组件验证背景：Python 依赖已安装在 conda `py312`（MinerU 4.1.7、vLLM 0.25.1），Qwen3.5-4B、Embedding、Reranker 及 MinerU 权重已下载。2026-11-13 按 6.1 节参数临时启动 Qwen3.5-4B，实际 `/v1/models` 返回 `rag-vlm`，文本、双图片及真实 PDF 裁剪图输入均通过；复测启动约 76–78 秒，显存约 14GiB，测试后服务已停止。该早期单组件阶段尚未验证其他端点；当前 8104 和业务链路结果见下一段。以下命令用于服务器 Linux Bash；其他显存预算和并发参数仍需实测，详见 [verification.md](verification.md)。

2026-11-13 部署追加：Qwen 8100 与 MinerU 专用 VLM 8104 已在项目独立部署中同时运行；后者实际模型 ID `mineru-vlm`、有效窗口 8192。原规划 16384 被 vLLM 拒绝，启动入口和下方命令已修正，没有强制覆盖模型限制。advanced 六页 PDF 已经真实 Kafka/Worker 完成并可检索；两模型驻留 GPU 约 24GB。完整步骤见 [服务器验收](server-acceptance.md)，不代表 Embedding/Reranker 或四模型共卡已验证。

## 1. 推荐组合与各阶段职责

首版采用 **Qwen3.5-4B + Qwen3-VL-Embedding-2B + Qwen3-VL-Reranker-2B + MinerU 配套模型**，先验证低并发共卡运行。质量升级候选为 Qwen3.5-9B；升级时重新分配显存，必要时将批量解析与问答错峰。

| 阶段 | 模型类型 | 推荐模型或组件 | 职责 |
| --- | --- | --- | --- |
| PDF 解析 | 版面、OCR、公式、表格小模型及文档专用 VLM | MinerU 配套小模型包 + 该版本匹配的专用 VLM；当前官方包装器默认 MinerU2.5-Pro-2605-1.2B | 提取正文、表格、公式、图片与位置关系 |
| 图片说明补充（可选） | 通用视觉语言模型 | Qwen/Qwen3.5-4B；升级候选 Qwen/Qwen3.5-9B | 对缺少说明的图片补充可检索文本；保留原图，说明不是唯一证据 |
| 分块与来源关联 | 无需额外模型 | 规则分块 | 按标题、段落、表格和图片组织证据，保留文档 ID、版本与页码 |
| 文档向量化 | 多模态 Embedding | Qwen/Qwen3-VL-Embedding-2B | 编码文本、图片及图文组合，首版规划 2048 维 |
| 用户问题理解（按需） | 通用视觉语言模型 | 复用 Qwen3.5-4B | 理解用户图片、改写多轮问题、提取检索关键词 |
| 查询向量化 | 多模态 Embedding | 同一个 Qwen3-VL-Embedding-2B | 编码文字和用户图片，与文档向量匹配 |
| 初步召回 | 无需额外生成模型 | Elasticsearch BM25 + 向量检索 | 召回关键词和语义相关的图文证据 |
| 重排序 | 多模态交叉编码器 | Qwen/Qwen3-VL-Reranker-2B | 对问题和候选图文证据评分 |
| 回答生成 | 通用视觉语言模型 | 复用 Qwen3.5-4B | 同时读取问题、用户原图、相关文档图文并生成回答 |
| 来源展示 | 无需模型 | 后端来源映射 + 前端渲染 | 将引用绑定到真实文档、页码和受控图片资源 |

Qwen3.5、Qwen3-VL-Embedding、Qwen3-VL-Reranker 是不同职责的模型，不能将聊天模型当作已训练的 Embedding 或 Reranker。首版不额外部署独立文本 Embedding；后续根据跨领域评估结果决定是否增加文本向量通道。

```text
PDF → MinerU CLI → 文本/表格/图片及来源 → 分块
                                      ├─ BM25 文本索引
                                      └─ 多模态 Embedding → ES 向量索引

文字 + 用户原图 → 按需理解问题 / 查询向量化
                → 限定文档范围 → BM25 + 向量召回 → 融合去重
                → 多模态 Reranker → 选取证据图文
                → Qwen3.5（问题 + 用户原图 + 证据图文）→ 回答及来源
```

查询与文档必须使用同一模型、维度、预处理和兼容提示模板。不同模型的向量即使维度相同也不能混用；切换模型或维度要重建索引或使用新的索引版本。文本块、图片块应按确定性 ID 保存，避免长文本挤掉图片信息。

## 2. 显存、上下文与并发起点

| 服务 | 初始 GPU 比例 | 按 48GB 粗算的预算 | 初始限制 |
| --- | --- | --- | --- |
| Qwen3.5-4B | 0.32 | 约 15.4GB | BF16，16K 上下文，最多 2 个序列，每请求最多 4 张图 |
| Embedding 2B | 0.15 | 约 7.2GB | BF16，8K 输入，最多 2 个序列，每输入最多 1 张图 |
| Reranker 2B | 0.15 | 约 7.2GB | BF16，8K 输入，最多 2 个序列，每评分对最多 2 张图 |
| MinerU 专用 VLM | 0.18 | 约 8.6GB | BF16，8K 上下文，最多 2 个序列 |
| 其余空间 | 约 0.20 | 约 9.6GB | MinerU 小模型、其他开销与余量 |

`--gpu-memory-utilization` 相对于整张 GPU，不是剩余显存，也不是跨进程显存硬隔离。以上是配置预算而非实测值，Pooling 服务也不保证按该比例实际占用。权重之外还包括 KV cache、视觉编码、中间计算及运行时开销，不能只按参数量判断是否能共卡。

- PDF Worker 初始并发为 1；MinerU 内部 VLM 请求并发为 2，两者分别控制。
- 先逐个启动并观察显存；启动与 profiling 期间不要让其他服务进行大批量推理。
- 初期使用 `--enforce-eager` 减少 CUDA Graph 相关变量，稳定后再评估性能取舍。
- 应用层控制图片像素、数量和证据长度。16K/8K 包含图像 token；生成模型还需留输出预算。
- 召回起点：BM25 与向量各取约 20 条，融合去重后重排前 20 条，选 5～8 个证据单元；图片另受 4 张上限约束。长输入需合理分块，不能静默截断关键图表。
- 若发生 OOM，先降低图片尺寸、批量、并发和上下文，再重分配预算；不得直接提高所有服务的 GPU 比例。
- 90GB 内存还要分配给 ES、MySQL、Kafka、PDF 预处理及系统缓存；20 核 CPU 不等于可以同时开启 20 个 PDF GPU 任务。

Qwen3.5-9B BF16 可在后续单独评估，主模型预算可从 0.50、低并发开始试验。不要把 0.50 直接替换进四模型共卡方案而保持其余参数不变。错峰需要真正释放项目解析模型的显存；仅停止提交解析任务不会卸载常驻权重。量化是后续候选，需核验具体权重格式、GPU 与 vLLM 支持并重测质量。

## 3. 服务器保存位置与端口

示例统一使用项目服务器根目录 `/workspace/multimodal-rag`。这不是已存在路径，也不是当前 Windows 工作区的映射；部署时可统一替换。不要改动原有 `/workspace/models/Qwen3.5-0.8B/` 或共享服务配置。

| 内容 | 默认规划保存位置 |
| --- | --- |
| 模型/项目 Python 环境 | 系统 conda `py312`：`/usr/local/miniconda3/envs/py312/`（Python 3.12.11） |
| 视觉问答权重 | `/workspace/multimodal-rag/models/Qwen3.5-4B/` |
| Embedding 权重 | `/workspace/multimodal-rag/models/Qwen3-VL-Embedding-2B/` |
| Reranker 权重 | `/workspace/multimodal-rag/models/Qwen3-VL-Reranker-2B/` |
| 可选 9B 权重 | `/workspace/multimodal-rag/models/Qwen3.5-9B/`，首版不下载 |
| Reranker 模板 | `/workspace/multimodal-rag/model-templates/qwen3_vl_reranker.jinja` |
| Hugging Face 缓存 | `/workspace/multimodal-rag/.cache/huggingface/` |
| MinerU 配置 | `/workspace/multimodal-rag/mineru/config.yaml` |
| MinerU 模型根目录 | `/workspace/multimodal-rag/mineru/models/`；实际子目录由该版本管理，用 `models show` 核对 |
| 环境版本与部署记录 | `/workspace/multimodal-rag/runtime/` |
| 原文件、解析产物、用户图片 | 项目 `data/`，按 `DATA_DIR` 统一配置；与模型权重分开 |

下载可能使用额外缓存，部署前检查磁盘容量。权重、缓存、运行配置及版本记录不提交 Git；源码目录 `backend/app/models/` 是业务数据模型，不能用全局 `models/` 忽略规则误伤它。

| 服务 | 监听位置 | 服务模型名 / 接口 |
| --- | --- | --- |
| 现有共享 vLLM | 原环境 `8000` | 保留原环境记录，不自动停止或覆盖 |
| 项目业务 API | `127.0.0.1:8001` | 后续实现 |
| 图文问答 | `127.0.0.1:8100` | `rag-vlm`，`/v1/chat/completions` |
| Embedding | `127.0.0.1:8102` | `rag-embedding`，`/v1/embeddings` |
| Reranker | `127.0.0.1:8103` | `rag-reranker`，`/score` 或 `/rerank` |
| MinerU 专用 VLM | `127.0.0.1:8104` | `mineru-vlm`，由 MinerU CLI 调用 |

端口改动不能隔离 GPU。若现有 `8000` 模型仍占同一张卡，必须将其占用算入预算；上述预算假设这 48GB 可供项目规划使用。是否调整共享进程需另行协调，本文不提供停止共享进程的命令。

## 4. 检查环境与准备依赖

先只读检查系统 conda 环境与硬件：

```bash
/usr/local/miniconda3/envs/py312/bin/python --version
/usr/local/miniconda3/envs/py312/bin/python -c "import importlib.metadata as m; print('vLLM:', m.version('vllm')); print('MinerU:', m.version('mineru'))"
command -v mineru-kit
nvidia-smi
ss -ltn
```

确认 GPU 实际型号、驱动、剩余显存、Python 版本以及上述端口是否空闲。当前指南按 MinerU 4.x 的 `mineru-kit` 接口编写；已有版本不匹配时先查看对应 `--help`，不要把旧版命令与新版配置混用。

本项目的模型服务和后端依赖都使用系统 conda `py312`，不再创建单独的 `.venv-models`。该环境当前为 Python 3.12.11，已安装 MinerU 4.1.7、vLLM 0.25.1 及项目后端依赖。项目与模型服务共用环境时，升级任一方的依赖都必须同时检查另一方，不能用旧版本清单把 vLLM 所需的 `fastapi` / `starlette` 降级。准备项目数据目录并核对依赖：

```bash
export RAG_ROOT=/workspace/multimodal-rag
export HF_HOME="$RAG_ROOT/.cache/huggingface"
export MINERU_HOME="$RAG_ROOT/mineru"

mkdir -p "$RAG_ROOT/models" "$RAG_ROOT/model-templates" \
  "$RAG_ROOT/runtime" "$MINERU_HOME" "$HF_HOME"
export MINERU_CONFIG="$MINERU_HOME/config.yaml"

conda activate py312
python -m pip install -r "$RAG_ROOT/backend/requirements-dev.txt"
python -m pip check
python -c "import importlib.metadata as m; print('vLLM:', m.version('vllm')); print('MinerU:', m.version('mineru'))"
```

`requirements-dev.txt` 已按 py312 中 vLLM 0.25.1 的要求调整。当前环境存在一条切换前已有的 `pip check` 冲突：MinerU 4.1.7 要求 `openai<3`，实际安装为 `openai 3.48.0`；它不影响已执行的导入和项目测试，但部署模型前应核实并记录处理方式，不能为了消除提示盲目降级而破坏 vLLM。

已有环境时先核对版本，避免原地升级或替换系统 conda 环境。当前 MinerU 的 Linux `full` 依赖组合包含 vLLM；CUDA/驱动、PyTorch、Transformers、MinerU 与 vLLM 必须兼容。验证通过后记录 `pip freeze`、驱动、模型仓库 revision、模板来源及启动参数到 `runtime/`，后续部署使用实际验证版本，不持续追随 latest。

## 5. 下载模型与模板

在上一节环境中执行。此阶段需要访问下载源；全本地指推理和业务数据处理在本机，不代表首次下载无需网络。离线服务器可先在联网机器下载并验证，再完整迁移权重、配置和匹配依赖。

```bash
hf download Qwen/Qwen3.5-4B \
  --local-dir "$RAG_ROOT/models/Qwen3.5-4B"

hf download Qwen/Qwen3-VL-Embedding-2B \
  --local-dir "$RAG_ROOT/models/Qwen3-VL-Embedding-2B"

hf download Qwen/Qwen3-VL-Reranker-2B \
  --local-dir "$RAG_ROOT/models/Qwen3-VL-Reranker-2B"

VLLM_VERSION="$(python -c 'import importlib.metadata as m; print(m.version("vllm"))')"
curl -fL \
  "https://raw.githubusercontent.com/vllm-project/vllm/v${VLLM_VERSION}/examples/pooling/score/template/qwen3_vl_reranker.jinja" \
  -o "$RAG_ROOT/model-templates/qwen3_vl_reranker.jinja"
```

开发版本、post 版本可能没有同名 Git 标签；若模板下载失败，先从对应发布源码取得模板，确认内容后再启动。不要把错误响应当模板，也不要随意混用 main 模板与旧版服务。正式复现时，`hf download` 应附加已验证的 `--revision <commit>`。

如果 Hugging Face 经当前代理速度过低，可使用 ModelScope 同名仓库，本次实际安装即采用该方式：

```bash
modelscope download --model Qwen/Qwen3.5-4B --local-dir "$RAG_ROOT/models/Qwen3.5-4B"
modelscope download --model Qwen/Qwen3-VL-Embedding-2B --local-dir "$RAG_ROOT/models/Qwen3-VL-Embedding-2B"
modelscope download --model Qwen/Qwen3-VL-Reranker-2B --local-dir "$RAG_ROOT/models/Qwen3-VL-Reranker-2B"
```

切换源后仍需核对文件名、大小和配置，不能把下载完成当作推理验证。下载记录见 [verification.md](verification.md)。

MinerU 配套模型先下载再配置服务端点；若预先配置 `server_url`，某些检查可能按外部 VLM 模式跳过本地 VLM 权重要求：

```bash
mineru-kit models download \
  --tier standard \
  --small-backend torch \
  --vlm-engine vllm \
  --source huggingface

mineru-kit models verify \
  --tier standard \
  --small-backend torch \
  --vlm-engine vllm

mineru-kit models show
```

MinerU 下载源可替换为 `modelscope`。其模型默认保存在 `$MINERU_HOME/models` 下；如果已有配置更改了 `model.base_dir`，以该配置及 `models show` 为准。模型包与 tokenizer/processor 配置要一起保留，不只复制权重文件。

## 6. 启动模型服务

每个服务使用独立终端或项目专属托管进程。先逐个启动，等待模型就绪后再启动下一个；以下命令为前台进程，不要在同一个终端顺序粘贴后期待四个服务自动全部启动。

每个终端先执行相同的环境设置（下载完成后使用离线读取）：

```bash
export RAG_ROOT=/workspace/multimodal-rag
export HF_HOME="$RAG_ROOT/.cache/huggingface"
export MINERU_HOME="$RAG_ROOT/mineru"
export MINERU_CONFIG="$MINERU_HOME/config.yaml"
export CUDA_VISIBLE_DEVICES=0
export OMP_NUM_THREADS=2
export HF_HUB_OFFLINE=1
export MINERU_MODEL_SOURCE=local
conda activate py312
```

### 6.1 图文理解与回答：8100

```bash
vllm serve "$RAG_ROOT/models/Qwen3.5-4B" \
  --served-model-name rag-vlm \
  --host 127.0.0.1 \
  --port 8100 \
  --dtype bfloat16 \
  --tensor-parallel-size 1 \
  --gpu-memory-utilization 0.32 \
  --max-model-len 16384 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 4096 \
  --limit-mm-per-prompt '{"image":4,"video":0}' \
  --reasoning-parser qwen3 \
  --enforce-eager
```

应用问答客户端已用该命令的 4 图上限完成真实图文验收，报告见 [verification.md](verification.md)。部署沿用此命令时，应用设 `IMAGE_MAX_CONTEXT_COUNT=4`；`.env.example` 的 8 图预算需与 vLLM 上限共同调整并单独压力验证。用户原图占满名额且检索到相关图时会报预算不足，需减少用户图。

该服务复用到问题理解、图片说明和最终回答。不能使用 `--language-model-only`，否则失去本项目需要的图片输入。调用时使用实际 `image_url` 内容块及受控 data URL，不能用文件名或图片说明替代原图。

### 6.2 多模态 Embedding：8102

```bash
vllm serve "$RAG_ROOT/models/Qwen3-VL-Embedding-2B" \
  --served-model-name rag-embedding \
  --runner pooling \
  --host 127.0.0.1 \
  --port 8102 \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.15 \
  --max-model-len 8192 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 8192 \
  --limit-mm-per-prompt '{"image":1,"video":0}' \
  --enforce-eager
```

使用 `/v1/embeddings` 的图文 `messages` 扩展，按对应版本的 Qwen 官方用例处理模板、空 assistant 尾消息、`continue_final_message` 和 `add_special_tokens`。普通文本 `input` 中的图片路径只是文本。当前设置每输入最多一张图，多图问题需在应用层限制或另行设计并验证聚合方式。

先核对实际输出为 2048 维及归一化策略，再建对应 ES mapping。数据库中的原图存储标识和检索向量分开保存，不能靠向量恢复原图。

### 6.3 多模态 Reranker：8103

```bash
vllm serve "$RAG_ROOT/models/Qwen3-VL-Reranker-2B" \
  --served-model-name rag-reranker \
  --runner pooling \
  --host 127.0.0.1 \
  --port 8103 \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.15 \
  --max-model-len 8192 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 8192 \
  --limit-mm-per-prompt '{"image":2,"video":0}' \
  --hf-overrides '{"architectures":["Qwen3VLForSequenceClassification"],"classifier_from_token":["no","yes"],"is_original_qwen3_reranker":true}' \
  --chat-template "$RAG_ROOT/model-templates/qwen3_vl_reranker.jinja" \
  --enforce-eager
```

`hf-overrides` 与模板用于相关性评分，不是普通聊天补全。调用 `/score` 或 `/rerank` 并按该版本支持的图文结构传候选证据；图像查询的评分结构也须单独验证。若接口只支持当前查询文本形式，应显式用视觉模型形成查询文本，同时保留用户原图给最终回答，不能宣称已经验证原图直接重排。

重排属于 `services/retrieval.py` 的后续职责，可再拆专用适配模块；当前没有已实现的 Rerank 客户端。

### 6.4 MinerU 专用 VLM：8104

```bash
mineru-kit vlm-server \
  --engine vllm \
  --host 127.0.0.1 \
  --port 8104 \
  --served-model-name mineru-vlm \
  --dtype bfloat16 \
  --gpu-memory-utilization 0.18 \
  --max-model-len 8192 \
  --max-num-seqs 2 \
  --max-num-batched-tokens 4096 \
  --enforce-eager
```

使用 MinerU 自己的包装器选择配套权重和 logits processor，不直接拿普通 Qwen 聊天服务替换。`8104` 是模型推理服务，不是 PDF 上传/解析 HTTP API。

## 7. MinerU 的模型与 CLI 配置

MinerU 内置的是解析流程及模型适配，权重通常需要另外下载。安装 Python 包不等于模型已经完整缓存。当前官方 4.x 的分级关系为：

| tier | 主要执行方式 |
| --- | --- |
| `flash` | 原生文本/文档解析，扫描件等场景使用 Flash OCR |
| `basic` | OCR、版面、公式、表格小模型，不要求本地 VLM |
| `standard` | 小模型 + 文档专用 VLM |
| `advanced` | 共用 Standard 模型和运行时，使用更多视觉推理计算；无需单独下载 Advanced 权重 |

当前官方默认小模型包包括 `MinerU-4_models_torch` / `MinerU-4_models_onnx`；本文选择 Torch。默认专用 VLM 为 `opendatalab/MinerU2.5-Pro-2605-1.2B`，最终以实际安装版本、下载清单与 `models show` 为准。它与图文问答的 Qwen3.5 是不同的权重。

完成模型下载和验证后，将以下配置合并到项目专用 `/workspace/multimodal-rag/mineru/config.yaml`；若文件已有内容，保留其他必要配置，不覆盖共享 `~/.mineru/config.yaml`：

```yaml
model:
  source: local
  small_backend: torch
  vlm:
    engine: vllm
    server_url: http://127.0.0.1:8104
    model: mineru-vlm
    max_concurrency: 2
    http_timeout: 600
```

默认 `model.base_dir` 为 `$MINERU_HOME/models`。如需修改位置，在下载前设置绝对路径，并与下载和启动环境保持一致。已有 `MINERU_CONFIG` 环境变量会选择另一个配置文件，应核对或显式指向上述项目文件。

`server_url` 使用本机回环地址，推理仍为全本地；配置后 CLI 调用已启动的专用 VLM，避免每个任务再加载一份 VLM。小模型仍在 CLI 解析进程中执行，占用要计入共卡预算。不要传 `--remote` 切换到云端解析。

在继承 `MINERU_HOME`、使用 conda `py312` PATH 的 Worker 中，继续使用 CLI：

```bash
mineru-kit parse /absolute/path/input.pdf \
  --format zip \
  -o /absolute/path/output.zip \
  --tier advanced
```

输入、输出是示意绝对路径，实际由应用生成内部 ID 并使用独立任务目录。保持参数数组调用与超时管理；不能拼接用户输入启用 `shell=True`。这里的 VLM HTTP 超时 600 秒与业务 CLI 总超时 1800 秒是不同层级，需按真实 PDF 时长调整。

当前保留项目已有 `advanced` 规划。可用相同样例比较 `standard` 后再决定默认级别；Advanced 并不保证每份 PDF 都更准确。ZIP、Markdown、JSON、页码与图片关系仍需实际核验，只有 ZIP 生成不能算任务完成。

## 8. 业务配置与启用条件

[.env.example](../.env.example) 已给出端口、模型别名和 2048 维的规划配置；问答和 MinerU 业务适配已实现，Embedding/Reranker 尚未实现。vLLM 本身不读取项目 `.env`；`scripts/start_vlm.sh`、`scripts/start_mineru_vlm.sh` 通过统一配置（支持 RAG_ENV_FILE）构造启动参数。MinerU 则使用自己的 `MINERU_HOME/config.yaml`，业务配置与 MinerU YAML 是不同文件。

| 业务配置项 | 规划值 | 启用条件 |
| --- | --- | --- |
| `LLM_BASE_URL` | `http://127.0.0.1:8100/v1` | 模型服务就绪 |
| `LLM_MODEL` | `rag-vlm` | 与 `/v1/models` 一致 |
| `LLM_VISION_ENABLED` | 初始 `false` | 真实图像输入测试通过后改为 `true` |
| `EMBEDDING_BASE_URL` | `http://127.0.0.1:8102/v1` | 图文向量接口与模板验证通过 |
| `EMBEDDING_MODEL` / `EMBEDDING_DIMENSIONS` | `rag-embedding` / `2048` | 与实际输出及 ES mapping 一致 |
| `RETRIEVAL_MODE` | 初始 `bm25`，目标 `hybrid` | 向量服务、ES 索引和过滤逻辑验证后切换 |
| `RERANK_BASE_URL` | `http://127.0.0.1:8103` | 后续客户端请求 `/score` 或 `/rerank` |
| `RERANK_MODEL` / `RERANK_ENABLED` | `rag-reranker` / 初始 `false` | 客户端实现、相关性排序和错误处理验证后开启 |
| `MINERU_HOME` / `MINERU_CONFIG` | `/workspace/multimodal-rag/mineru` / 其下 `config.yaml` | Worker 必须向子进程传递这两个环境变量 |
| `MINERU_MAX_CONCURRENCY` | `1` | 与内部 VLM 并发 2 分别控制 |

配置地址不等于服务可用。不能用随机向量、假评分或文本请求成功替代图文能力测试。运行时服务故障应明确报告或按已定义策略降级，不得悄悄更换检索模式并宣称功能正常。

## 9. 后续服务器验证与记录

模型列表只证明端点能够响应，不证明质量或图文输入成功：

```bash
curl -fsS http://127.0.0.1:8100/v1/models
curl -fsS http://127.0.0.1:8102/v1/models
curl -fsS http://127.0.0.1:8103/v1/models
curl -fsS http://127.0.0.1:8104/v1/models
```

以上示例仅绑定本机且未配置 API key；若实际服务启用认证，客户端使用服务器本地配置中的凭据，不把密钥写进文档。

必要的验证顺序：

1. 记录 GPU/驱动、空闲显存、端口及实际依赖版本；确认已有共享进程的资源占用。
2. 单服务逐个验证，再验证四服务常驻与峰值显存；启动 profiling 和正常运行都要观察。
3. 给主模型真实图片及问题，核验可见细节；测试用户图和证据图一起输入。
4. 用相关与无关文本/图片测试 Embedding，核验向量维度、模板和排名；确认文档与查询预处理一致。
5. 用图文候选测试 Reranker 的排序、长输入、图片上限及错误响应。
6. 用扫描页、双栏、公式、表格和图表 PDF 验证 MinerU，核验完整页数、图片、来源和 ZIP 安全处理。
7. 完成 ES mapping、索引写入、当前版本/已完成状态/文档范围过滤后，再启用混合检索和重排。
8. 同时执行一个 PDF 解析与问答，记录峰值显存、延迟、OOM、失败率与重试行为，调整预算和并发。
9. 固定验证通过的依赖及模型 revision，记录 `standard` / `advanced` 的效果与耗时对比。

正式运行时由项目专属进程管理器托管上述独立服务，并配置日志、工作目录和环境；当前不假设服务器已有特定管理器，也未生成启动脚本。Windows 本地文档检查不等于上述服务器验收。

## 10. 官方参考

以下资料在本次选型讨论中核对；在线资料会更新，不代表服务器已有对应版本。

- [Qwen3.5-4B](https://huggingface.co/Qwen/Qwen3.5-4B)、[Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)：视觉问答模型。
- [Qwen3-VL-Embedding-2B](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)、[Qwen3-VL-Reranker-2B](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B)：多模态检索和排序模型。
- [vLLM Qwen3.5 部署](https://docs.vllm.ai/projects/recipes/en/latest/Qwen/Qwen3.5.html)、[引擎参数](https://docs.vllm.ai/en/stable/configuration/engine_args/)：运行参数与资源配置。
- [Embedding API](https://docs.vllm.ai/en/latest/models/pooling_models/embed/)、[图文 Embedding 用例](https://github.com/vllm-project/vllm/blob/main/examples/pooling/embed/vision_embedding_online.py)：输入格式和模板处理。
- [Scoring API](https://docs.vllm.ai/en/latest/models/pooling_models/scoring/)、[Qwen3-VL 重排模板](https://github.com/vllm-project/vllm/blob/main/examples/pooling/score/template/qwen3_vl_reranker.jinja)：评分配置及输入格式。
- [MinerU 安装](https://opendatalab.github.io/MinerU/quick_start/)、[依赖声明](https://github.com/opendatalab/MinerU/blob/master/pyproject.toml)：隔离环境与版本依赖。
- [MinerU tiers](https://opendatalab.github.io/MinerU/usage/tiers/)、[模型下载及配置](https://opendatalab.github.io/MinerU/usage/model_source/)、[CLI](https://opendatalab.github.io/MinerU/usage/cli_tools/)：解析分级、模型位置和调用方式。
- [MinerU VLM 启动实现](https://github.com/opendatalab/MinerU/blob/master/mineru/kit/vlm_server/vllm_server.py)、[专用 VLM 模型卡](https://huggingface.co/opendatalab/MinerU2.5-Pro-2605-1.2B)：默认专用模型及 logits processor。
