# vLLM 接入

全本地部署的模型选择、权重路径、conda 环境、下载与启动命令见 [模型部署指南](../../docs/models.md)。后续由 `backend/app/services/llm_client.py`、`embedding.py`、`retrieval.py` 分别适配图文问答、向量和重排，当前均未实现。

| 用途 | 规划模型 | 端点 | 别名 |
| --- | --- | --- | --- |
| 图文问答 | Qwen3.5-4B | `http://127.0.0.1:8100/v1` | `rag-vlm` |
| 图文向量 | Qwen3-VL-Embedding-2B | `http://127.0.0.1:8102/v1` | `rag-embedding` |
| 图文重排 | Qwen3-VL-Reranker-2B | `http://127.0.0.1:8103/score` 或 `/rerank` | `rag-reranker` |

项目和模型统一使用系统 conda `py312`（`/usr/local/miniconda3/envs/py312`），上述权重保存在项目根目录的 `models/` 下。MinerU 专用 VLM 另使用 `8104`，见 [MinerU 接入](../mineru/README.md)。单卡 48GB 显存需按指南分配，不使用多个默认 0.9 预算并发启动。

原环境的 `http://localhost:8000/v1` 及以下命令仅保留为环境记录，不自动覆盖或重启：

```sh
cd /workspace
vllm serve models/Qwen3.5-0.8B/
```

上述 8000 命令仅为历史参考，未重启或覆盖原服务。2026-11-13 已临时在 `127.0.0.1:8100` 启动 Qwen3.5-4B，`/v1/models` 返回 `rag-vlm`，健康、文本、双图片及真实 PDF 图片输入通过；测试后端口和显存均已释放。详见 [验收记录](../../docs/verification.md)。`LLM_MODEL` 应与实际返回 ID 一致；测试服务未启用 API key，`LLM_API_KEY=EMPTY` 仅适用于这类部署。

主模型真实图片能力已验证；应用 `LLM_VISION_ENABLED` 仍为 false，需在常驻服务与业务图像客户端就绪后启用。向量接口、重排接口和 8104 尚未验证，保持 BM25 与 `RERANK_ENABLED=false`；单模型验收不代表完整 RAG 联调或四模型共卡通过。
