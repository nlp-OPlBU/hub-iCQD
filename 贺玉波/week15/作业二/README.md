# week15 作业2 · Dockerfile 打包

基于 `作业一/openai_agents_version` 的代码，用 Dockerfile 构建单镜像；镜像内由 `start.py` 拉起三个进程，只对外暴露后端 8200。

## 一、交付物

| 文件 | 作用 |
|---|---|
| `Dockerfile` | 多阶段构建：builder 只装依赖，runtime 以非 root 运行 |
| `.dockerignore` | 排除 `.env`、`__pycache__`、`cli.py`、`run_all.py` 等 |
| `start.py` | 容器入口，启动进程1(8201)、进程2(8202)、后端(8200)，并处理 SIGTERM |
| `config.py` / `schemas.py` / `agent_runtime.py` / `*_service.py` / `backend.py` | 从作业一复制的 openai-agents 版运行代码 |
| `static/index.html` | 前端页面，由后端托管 |
| `requirements.txt` | 运行时依赖 |
| `.env.example` | 环境变量模板 |

## 二、镜像结构

```
容器
├── start.py（PID 1）
├── 进程1 情感识别  127.0.0.1:8201
├── 进程2 鼓励/排解 127.0.0.1:8202
└── 后端 + 网页     0.0.0.0:8200   ← 唯一对外端口
```

只有 8200 映射到宿主机；8201、8202 绑定 `127.0.0.1`，容器外不可直接访问。

## 三、构建

前置：Docker Desktop 已启动（`docker version` 能看到 Server）。

```powershell
cd a:\八斗学院\hub-iCQD\贺玉波\week15\作业二
docker build -t sentiment-agents:1.0.0 .
```

国内网络慢时：

```powershell
docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple `
  -t sentiment-agents:1.0.0 .
```

## 四、运行

先准备 `.env`（本目录不存放真实 key）：

```powershell
copy ..\作业一\openai_agents_version\.env .env
# 或 copy .env.example .env 后手动填写 DEEPSEEK_API_KEY
```

启动：

```powershell
docker run -d --name sentiment-agents -p 8200:8200 --env-file .env sentiment-agents:1.0.0
```

没有 key、只想演示链路：

```powershell
docker run -d --name sentiment-agents -p 8200:8200 -e MOCK=1 sentiment-agents:1.0.0
```

打开 <http://127.0.0.1:8200>，输入文本后点“开始分析”。

## 五、验证

```powershell
docker ps
```
等约 25 秒，状态应为 `Up ... (healthy)`。

```powershell
docker logs -f sentiment-agents
```
应看到 `[launcher] sentiment/response/backend started` 和三段 `Uvicorn running`。

```powershell
Invoke-RestMethod http://127.0.0.1:8200/health | ConvertTo-Json -Depth 5
```
预期三个服务均为 `ok`。

作业验收点：

```powershell
docker top sentiment-agents                 # 1 个 start.py + 3 个 uvicorn
docker exec sentiment-agents id             # uid=1001(appuser)，非 root
docker run --rm sentiment-agents:1.0.0 ls -a /app
docker run --rm sentiment-agents:1.0.0 python -c "import importlib.util; print(importlib.util.find_spec('app'))"
```

- `/app` 中不应出现 `.env`；
- `find_spec('app')` 应输出 `None`（镜像里没有源码包）。

停止与清理：

```powershell
docker stop sentiment-agents
docker rm sentiment-agents
```

## 六、推送到阿里云 ACR 个人版

1. 打开 <https://cr.console.aliyun.com>，进入**个人版**；
2. 「命名空间」创建，例如 `heyubo`（必须小写）；
3. 「镜像仓库」创建 `sentiment-agents`；
4. 「访问凭证」设置 Registry 密码（不是阿里云登录密码）。

```powershell
docker login --username=<阿里云账号> registry.cn-hangzhou.aliyuncs.com

docker tag sentiment-agents:1.0.0 registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-agents:1.0.0
docker push registry.cn-hangzhou.aliyuncs.com/<命名空间>/sentiment-agents:1.0.0
```

其他地域把域名换成 `registry.cn-beijing.aliyuncs.com`、`registry.cn-shanghai.aliyuncs.com` 或 `registry.cn-shenzhen.aliyuncs.com`。

推送完成后，ACR 控制台仓库中应能看到 `1.0.0` 标签。

## 七、实现说明与踩坑记录

- **多阶段构建**：builder 只执行 `pip install -r requirements.txt`，runtime 复制 `/install` 到 `/usr/local`，镜像里没有构建缓存。
- **非 root**：`useradd -m -u 1001 appuser`，`/app` 归属 appuser，进程以 UID 1001 运行。
- **进程管理**：最初用 supervisord，但 `supervisor 4.2.5` 依赖 `pkg_resources`，而新版 `setuptools` 已移除该模块，容器启动即报 `ModuleNotFoundError`。改为自带的 `start.py` 拉起并监控三个 uvicorn，零额外依赖。
- **健康检查**：`HEALTHCHECK` 请求 `/health` 并解析 JSON，只有三个服务都 `ok` 才判定健康。
- **密钥**：`.env` 被 `.dockerignore` 排除，运行时通过 `--env-file` 注入，不进入镜像层。

## 八、实测记录

| 检查 | 结果 |
|---|---|
| `docker build` 成功 | 通过 |
| `docker ps` 显示 `Up (healthy)` | 通过 |
| `/health` 三个服务均 `ok` | 通过 |
| 真实 DeepSeek 调用（正向输入） | 通过，置信度 0.99，耗时约 3.5s |
| 网页端到端 | 通过 |
| 非 root（uid=1001） | 通过 |
| 镜像内无 `.env`、无 `app` 包 | 通过 |
