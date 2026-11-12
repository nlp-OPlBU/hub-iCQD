# MinerU CLI 接入

后续实现入口为 `backend/app/services/mineru_cli.py`。完整的模型下载、保存位置、vLLM 启动和配置示例见 [全本地模型部署指南](../../docs/models.md)。当前仅有文档，未验证服务器安装版本或解析能力。

用户环境提供的命令格式（仅作参考，本次未执行）：

```sh
cd /workspace
mineru-kit parse pdf/test2.pdf --format zip -o output.zip --tier advanced
```

应用需要替换成每任务独立的输入、输出绝对路径，并通过参数数组启动子进程。`MINERU_COMMAND`、`MINERU_WORKING_DIR`、`MINERU_HOME`、`MINERU_TIER`、超时和并发通过统一配置控制；Worker 向子进程传递 `MINERU_HOME`，并使用对应独立环境的 CLI。

新方案规划在 `/workspace/multimodal-rag/mineru/` 保存配置和配套权重，由独立 `mineru-kit vlm-server --engine vllm` 在 `127.0.0.1:8104` 提供专用模型推理。CLI 根据 `config.yaml` 的 `model.vlm.server_url` 调用它。该端点不是 PDF 上传或解析 HTTP API，当前也未启动；不会因此改变业务的 CLI 适配方式。

MinerU 内置解析流程及模型适配，权重需另行下载并验证。当前官方 4.x 中，Basic 使用小模型，Standard 与 Advanced 使用小模型及专用 VLM；Advanced 共用 Standard 权重，没有额外一套下载。具体模型由安装版本管理，不能将普通 Qwen 聊天模型直接当成专用解析模型。

后续用实际 ZIP 验证 Markdown、JSON、图片、页码关系与安全解压。Worker 初始并发 1，内部 VLM 请求并发规划 2；与其他模型的 GPU 共用预算待实测。此目录目前不提供模型安装或服务启动脚本。
