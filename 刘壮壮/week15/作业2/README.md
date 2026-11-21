# 作业 2：基于 OpenAI Agents 版本的 Docker 镜像打包

## 文件位置说明

按工程日常习惯，为了保持 Docker 构建上下文干净且避免业务代码重复拷贝，[`Dockerfile`](../作业1/openai_agents/Dockerfile) 与 [`.dockerignore`](../作业1/openai_agents/.dockerignore) 直接放置在作业 1 的 OpenAI Agents 项目根目录下：

- Dockerfile：`../作业1/openai_agents/Dockerfile`
- Docker 忽略文件：`../作业1/openai_agents/.dockerignore`

## Dockerfile 说明

- **基础镜像**：`python:3.12-slim`
- **工作目录**：`/app`
- **依赖安装**：先拷贝 `requirements.txt` 安装 Python 依赖，再拷贝 `backend/` 与 `frontend/`，利用 Docker 分层缓存加速构建
- **默认环境变量**：`BACKEND_HOST=0.0.0.0`、`BACKEND_PORT=8000`
- **暴露端口**：`8000`
- **健康检查**：定期请求 `http://127.0.0.1:8000/health`
- **启动命令**：`python -m backend.main`
- **安全隔离**：通过 `.dockerignore` 排除 `.env`、`__pycache__/`、`.venv/` 等本地文件，API Key 在运行时通过环境变量注入

## 本地构建与运行

### 1. 构建镜像

从当前 `作业2` 目录切换到 `openai_agents` 目录执行：

```bash
cd ../作业1/openai_agents
docker build -t sentiment-openai-agents:latest .
```

### 2. 启动容器

通过 `--env-file` 或 `-e` 传入 `DEEPSEEK_API_KEY` 等环境变量（注意不要把 `.env` 打进镜像）：

```bash
docker run -d \
  --name sentiment-api \
  -p 8000:8000 \
  --env-file .env \
  sentiment-openai-agents:latest
```

### 3. 验证服务

1. 检查容器状态与健康检查：

```bash
docker ps
curl http://127.0.0.1:8000/health
```

2. 调用情感对话接口：

```bash
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"text": "今天终于把项目上线了，好开心！"}'
```

3. 启动前端 CLI 联调（在宿主机或容器内均可）：

```bash
python frontend/app.py
```

## 阿里云容器镜像服务（ACR）推送示例

如需将镜像推送到阿里云容器镜像服务，可执行以下命令（将 `<registry>`、`<namespace>`、`<repo>` 替换为实际仓库信息）：

```bash
docker login --username=<你的阿里云账号> <registry>
docker tag sentiment-openai-agents:latest <registry>/<namespace>/<repo>:v1.0.0
docker push <registry>/<namespace>/<repo>:v1.0.0
```
