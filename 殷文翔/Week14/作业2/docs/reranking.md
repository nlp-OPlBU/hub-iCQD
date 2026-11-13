# 图文重排与完整问答链路

本机使用 Qwen3-VL-Reranker-2B、vLLM 0.25.1 pooling、`rag-reranker`、`127.0.0.1:8103/score`。调用协议及分类配置依据 [Qwen 官方模型说明](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B) 和[对应版本的评分模板](https://github.com/vllm-project/vllm/blob/v0.25.1/examples/pooling/score/template/qwen3_vl_reranker.jinja)，通过真实文本、图像候选和图像查询验证。模型以 no/yes 分类输出相关性分数；分数不是事实正确率。

## 处理与降级

1. MySQL 限定已完成、当前有效的文档版本；用户选择的范围同时用于 BM25 和向量检索。
2. 两路召回后 RRF 融合，取 `max(RETRIEVAL_CANDIDATES, 请求 limit)` 个候选。重排前对每个候选核对 normalized.json 中的文本、页码、位置及图片归属。
3. `/score` 接收查询文字和用户实际图片，以及候选文字和受控 JPEG data URL。候选图片最长边 768、每张最多 1024 个估算 token，并校验文件 SHA256。纯图片候选也可直接通过评分接口参与。文本块可带已验证的同页图片，图片/表格块带自身图片。
4. 按 index 还原返回分数，验证模型、数量、唯一索引、有限值及 0–1 范围；全部批次成功后稳定降序排序，选 Top-K。保留 `retrieval_score`、`rerank_score`，不改写 ES 或 MySQL 解析状态。
5. 超时、连接失败、HTTP 错误、模型/分数异常、查询超过重排预算时，整组保留原召回顺序；丢弃全部部分评分，不伪造分数。搜索响应提供 `rerank_status=degraded` 和中文 `warning`，问答 Markdown 首部附加降级提示并随消息持久化。来源校验失败直接拒绝，不能用降级绕过。BM25/Embedding/ES 失败仍明确报检索不可用。
6. RAG 把问题、用户原图副本、选中的文字和文档图片一起送给 8100 视觉模型。原图保留在存储中，模型副本按预算缩放。来源只来自实际送入的证据，图片块未进入视觉上下文时不能成为来源。图片标签使用证据编号，系统列出本次允许引用的编号；未知引用仍拒绝。明确“依据不足”的回答不展示无关来源和图片。引用图片 URL 由资源记录生成，前端安全渲染 Markdown，可打开原始 PDF、页码和证据位置。

未实现相关性阈值过滤或通用质量评测：混合检索可能召回不相关候选，模型必须依据实际内容拒答；分数高不代表有答案。当前上下文预算可能选取少于 Top-K 的证据。

## 配置与启停

`.env.example` 保持 BM25/重排关闭；本机专属 `runtime/deployment/project.env` 启用 hybrid 与 rerank。新增配置：

| 配置 | 默认值 |
| --- | --- |
| RERANK_ENABLED | false |
| RERANK_BASE_URL / RERANK_MODEL | http://127.0.0.1:8103 / rag-reranker |
| RERANK_API_KEY | EMPTY；实际凭据只保存在私有环境文件 |
| RERANK_TIMEOUT_SECONDS | 60，所有 HTTP 批次共用期限 |
| RERANK_BATCH_SIZE | 4 |
| RERANK_MAX_IMAGES | 2，查询和每个候选各自上限；查询超限明确降级 |
| RERANK_MODEL_PATH | models/Qwen3-VL-Reranker-2B |
| RERANK_TEMPLATE_PATH | model-templates/qwen3_vl_reranker.jinja |
| RERANK_GPU_MEMORY_UTILIZATION | 0.15 |

候选图片超过上限时按已验证顺序取前 N 张；查询图片不静默截断。候选评分文本上限 12000 字符；实际服务上下文为 8192，超出服务预算时明确降级。最终回答仍受独立的图像数量、字节、token 及总上下文预算控制。

```bash
export RAG_ENV_FILE=runtime/deployment/project.env
export PATH=/usr/local/miniconda3/envs/py312/bin:$PATH
python scripts/start_reranker.py  # 前台启动本项目 loopback 模型
bash start.sh --check            # 只读检查包含 reranker
```

`bash start.sh` 会按配置启动缺失的 Reranker；已有端口占用时不覆盖进程。修改 RERANK_ENABLED 后需优雅重启本项目 API，启动脚本不会重启已运行 API。回退设置 `RERANK_ENABLED=false` 并重启 API，可保留重排模型运行。`stop.sh` 继续保留全部模型。没有改动共享 Nginx、Kafka、ES 或其他模型配置。

## 真实验收与复现

使用已完成的六页 `test2.pdf`，传入对应文档 ID。脚本创建验收会话和一张问题图片，故障检查使用单独应用配置指向关闭的本机端口，不停止真实 Reranker。

```bash
python scripts/verify_reranker.py --document-id <已完成的test2.pdf文档ID> \
  --output runtime/reranker/acceptance.json
```

2026-11-13 实测：表格问答说明首位为第 3 页，图像查询 Mate40 价格首位为第 4 页表格；真实回答为 6999，来源、页码、图像资源均可核对。原始召回中第 4 页表格位于文字说明之后，重排将其提升至首位。真实模型输入包含用户图与文档图的 data URL；无依据问题明确拒答；重排连接故障返回原召回顺序并显示降级提示。报告在忽略目录 `runtime/reranker/acceptance.json`。

四个模型同时驻留时 GPU 约 39108 / 49140 MiB。这是单用户验收，不代表四模型峰值并发、长期稳定性或大规模相关性评估通过。非 root、开机托管、鉴权、公网 TLS 仍未实现。

浏览器用例 `frontend/server-tests/reranker.spec.ts` 使用 `RAG_SERVER_URL`、`RAG_RERANK_DOCUMENT`、`RAG_SERVER_IMAGE` 显式选择已部署的页面、六页样本文档和第 4 页手机表格图片。运行 `cd frontend && npm run test:server -- reranker.spec.ts`，本环境需沿用 [服务器验收](server-acceptance.md) 中的 Chromium 本地库路径。用例核验 HTTP 重排状态、真实问题图片上传、6999 回答、图片加载、引用新标签页定位第 4 页和无依据拒答。
