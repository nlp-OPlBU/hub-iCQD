# 图文向量、回填与混合检索

> 2026-11-13 图文重排更新：8103 Reranker 已部署，已接入混合候选评分与 RAG，真实图文问答及显式降级已验证。此前“重排未接入/待部署”描述由[图文重排说明](reranking.md)覆盖；并发与开机托管仍未验收。

2026-11-13 接入 Qwen3-VL-Embedding-2B（本机 8102，rag-embedding，2048 维）。文本与真实图片均发送到同一模型；图片采用受控 JPEG data URL，最长边 768。多图逐个编码文本+图片，再取均值归一化；文档与查询策略一致。模板依据 [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) 并经本机 vLLM 0.25.1 验证。超时、维度错误、非有限值或零向量报错，不生成替代向量。

## 索引与一致性

原 `ES_INDEX` 持续提供 BM25，是 MySQL 中 task.index_name 对应的基础索引。task.retrieval_mode 保存完成时的模式，历史 bm25 和新增 hybrid 完成任务都可参与当前检索；在线问答模式由 RETRIEVAL_MODE 控制，不改写历史任务。新增任务启用向量后，基础索引和所有向量写入目标均验证可检索，才提交 completed。

独立向量索引存储同一分块及 cosine dense_vector。`VECTOR_READ_ALIAS` 指向单个活动代；`VECTOR_WRITE_ALIAS` 可指向多个候选/回退代，Worker 逐个写入。原索引和旧向量代均不删除。BM25、向量两路并发，采用相同 MySQL completed/current 版本及用户范围，kNN 预过滤，结果再校验。RRF 按 document_id/version/chunk_id 去重融合；不是重排模型。任一路失败报检索不可用，不静默退化。

模型别名、维度和编码策略写入 mapping `_meta` 并在读写时检查。更换权重必须使用新模型别名、新读写 alias 和新索引，不得在相同模型别名下替换权重；本轮没有支持不同模型同时双写。

## 配置

示例配置保持 BM25/关闭向量。实际部署在忽略目录 runtime/deployment/project.env 启用。

| 配置 | 默认值/含义 |
| --- | --- |
| EMBEDDING_MODEL_PATH | models/Qwen3-VL-Embedding-2B，按项目根目录解析 |
| EMBEDDING_BASE_URL / MODEL / DIMENSIONS | http://127.0.0.1:8102/v1 / rag-embedding / 2048 |
| EMBEDDING_TIMEOUT_SECONDS | 120 |
| EMBEDDING_GPU_MEMORY_UTILIZATION | 0.15 |
| VECTOR_ENABLED | false；true 时新增任务同步写所有向量目标 |
| VECTOR_READ_ALIAS | multimodal_rag_vectors_active |
| VECTOR_WRITE_ALIAS | multimodal_rag_vectors_writes |
| RETRIEVAL_CANDIDATES / RETRIEVAL_RRF_K | 40 / 60 |

## 操作顺序

以下命令从项目根目录运行，使用项目专属配置及系统 py312；准备/回填/验证可重复执行。

```bash
export RAG_ENV_FILE=runtime/deployment/project.env
export PATH=/usr/local/miniconda3/envs/py312/bin:$PATH
python scripts/start_embedding.py
# 模型进程前台运行；另一终端执行以下命令
python scripts/vector_index.py prepare multimodal_rag_vectors_active_20261113_v1
```

设置 VECTOR_ENABLED=true，优雅重启所有 Worker（确保旧 Worker 退出），再执行回填。首次上线仍保持 RETRIEVAL_MODE=bm25。新代准备期间不要并发运行另一轮索引迁移；最终切换前暂停接收新上传并等待 processing 任务结束，避免准备新代时已在处理的旧 Worker 漏写新代。

```bash
python scripts/vector_index.py backfill multimodal_rag_vectors_active_20261113_v1
python scripts/vector_index.py verify multimodal_rag_vectors_active_20261113_v1
python scripts/vector_index.py activate multimodal_rag_vectors_active_20261113_v1
```

回填从 MySQL 当前完成版本读取 normalized.json，不重新解析 PDF。成功写入的每个分块携带源内容/模型契约 SHA256 指纹，作为 ES 持久检查点；进程中断后重跑 backfill，仅补缺失或变化项。每次写入 refresh 并验证可检索，失败输出文档 ID、版本、错误类别，返回非零；修复故障后显式重跑，避免无限重试。新版本使用确定性独立 ID，旧版本由检索过滤隔离。

activate 再次扫描所有当前完成文档，缺失任何分块拒绝切换，ES 单次 `_aliases` 原子切换。之后设置 RETRIEVAL_MODE=hybrid 并重启 API。bash start.sh 会按需启动 Embedding，但不会重启已运行进程或自动执行回填。

回退：保持 VECTOR_ENABLED=true，把 RETRIEVAL_MODE 改回 bm25 并重启 API，即回到持续更新的原索引。已有旧向量代时，对旧代执行 verify/activate 可原子回退；保留在写 alias 的旧代持续获得新增数据。不得随意移除旧代的写 alias 后假定它仍包含新文档。切换命令不会删除索引、产物或修改任务状态。

## 实测与限制

真实文本 2048 维、单位范数通过。8 份历史文档共 390 分块完成；写入 352 块时终止进程，恢复后为 390 块，无重复。测试报告位于忽略目录 runtime/embedding，避免提交文档原文和日志。

`python scripts/verify_vectors.py` 使用已上传的 test2.pdf：TableQA 命中第 2 页标题；语义改写“如何从表格数据里找到问题的答案”首位命中第 3 页解释；知识图谱问题命中第 4 页。真实第 3 页图片的纯图片向量和图文混合查询均首位命中第 3 页；错误版本/不存在文档返回空。该脚本仅适用于含上述验收样本的部署。

仍需独立评估大规模召回质量、最大并发和长时稳定性；未接入 Reranker。当前回填按分块同步调用模型，优先保证可恢复性，未做批量 GPU 吞吐优化。多代双写增加存储和写入开销，回退窗口结束后由运维明确退役旧代。


### 增量、切换和 HTTP 问答实测

追加上传一页 QA PDF，经真实 basic CPU 解析和 Worker 完成落库，新增 5 个分块可以混合检索；总计 9 份完成文档、395 分块。第二代从同模型索引复制并按 normalized.json 全量指纹核验、增量补齐。v1→v2→v1→v2 的 ES 原子 alias 操作每次后均验证新增文档仍可检索，最终活动代为 `multimodal_rag_vectors_active_20261113_v2`；两代均留在写 alias。原 BM25 索引回退查询通过。

切换验收在确认两个代均早于当前 Worker 处理尝试创建、且完成文档全部覆盖后，调用同一底层 activate 操作执行；日常 CLI 的保守保护会拒绝仍有 processing 任务时切换，避免操作人员误将旧进程的半完成结果纳入新代。原有长任务在 Worker 更新后由 outbox 租约恢复，目前不能把它记作本轮解析成功。

真实 HTTP `/search` 返回 RRF 分数和第 3 页表格证据；会话创建、用户图片上传、图文提问链路成功，回答识别 FAQ 问答技能并返回真实来源和受控图片链接。GPU 三个模型服务驻留实测约 31522 MiB / 49140 MiB，不代表峰值并发验收。

证据：`runtime/embedding/{backfill.json,retrieval-report.json,incremental-report.json,switch-report.json,http-search.json,chat-report.json}`。最终完整后端检查 156 passed、3 skipped（显式临时 MySQL，包含 10 个向量专项检查）；前端 npm run build 通过。未把跳过的外部服务测试当作通过。
