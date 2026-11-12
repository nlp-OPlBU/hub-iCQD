# vLLM 接入

全本地部署的模型选择、权重路径、独立环境、下载与启动命令见 [模型部署指南](../../docs/models.md)。后续由 `backend/app/services/llm_client.py`、`embedding.py`、`retrieval.py` 分别适配图文问答、向量和重排，当前均未实现。

| 用途 | 规划模型 | 端点 | 别名 |
| --- | --- | --- | --- |
| 图文问答 | Qwen3.5-4B | `http://127.0.0.1:8100/v1` | `rag-vlm` |
| 图文向量 | Qwen3-VL-Embedding-2B | `http://127.0.0.1:8102/v1` | `rag-embedding` |
| 图文重排 | Qwen3-VL-Reranker-2B | `http://127.0.0.1:8103/score` 或 `/rerank` | `rag-reranker` |

独立模型环境规划为 `/workspace/multimodal-rag/.venv-models/`，上述权重保存在项目服务器根目录的 `models/` 下。MinerU 专用 VLM 另使用 `8104`，见 [MinerU 接入](../mineru/README.md)。单卡 48GB 显存需按指南分配，不使用多个默认 0.9 预算并发启动。

原环境的 `http://localhost:8000/v1` 及以下命令仅保留为环境记录，不自动覆盖或重启：

```sh
cd /workspace
vllm serve models/Qwen3.5-0.8B/
```

该命令只是参考，本次未启动或重启模型。`LLM_MODEL` 应与实际 `/v1/models` 返回 ID 一致；`LLM_API_KEY=EMPTY` 只适用于未启用认证的部署。

后续需要对真实图片执行能力验证，确认模型、vLLM 版本与配置兼容，再开启 `LLM_VISION_ENABLED`。向量接口和重排接口需分别验证，完成前保持 BM25 与 `RERANK_ENABLED=false`；模型地址与下载命令不是能力已可用的证明。
