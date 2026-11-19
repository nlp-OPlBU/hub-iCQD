# 作业2 · 构建 OpenAI Agents 镜像

[Dockerfile](Dockerfile) 直接使用作业1的后端、网页和 OpenAI Agents 依赖。容器以普通用户运行，启动时只启用 OpenAI Agents，健康检查访问 `/api/health`。

## 构建与运行

以下命令均在 `week15` 目录执行。最后的 `.` 是构建上下文，Docker 据此读取同级作业1的代码。

```bash
docker build -t wxw-week15-emotion-agents:local -f "作业2/Dockerfile" .
```

先按[作业1说明](../作业1/README.md)准备 `作业1/.env`，再向容器注入配置。

```bash
docker run --rm --name week15-emotion-agents \
  --env-file "作业1/.env" -p 127.0.0.1:8000:8000 \
  wxw-week15-emotion-agents:local
```

打开 [本地容器网页](http://127.0.0.1:8000) 体验，或在另一个终端检查。

```bash
curl -fsS http://127.0.0.1:8000/api/health
curl -fsS http://127.0.0.1:8000/api/reply \
  -H 'Content-Type: application/json' \
  -d '{"framework":"agents","text":"面试没通过，我很失落。"}'
```

健康接口预期返回 `{"status":"ok","frameworks":["agents"]}`。上面的回应用例预期得到 `negative` 标签和一段排解建议。

## 实际打包记录

- 2026-11-18 使用 Docker 29.8.2 构建成功，本地标签为 `wxw-week15-emotion-agents:local`，平台为 `linux/arm64`。
- 镜像 ID 为 `sha256:be40b5548455effb2021e79c472d806e2002b51d611a3d8a107dd5a655919347`。
- 容器健康状态为 `healthy`，以 UID 10001 运行。通过模拟模型端点验证了正向鼓励、负向排解、两阶段调用顺序、输入校验和模型故障后恢复。实际调用模型时需提供有效的 API Key。
