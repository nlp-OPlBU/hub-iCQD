# 楼道消防安全风险识别（前后端多模态应用）

参考 `案例-多模态内容理解.ipynb` 中的「楼道消防安全隐患识别」任务，构建的一个带前后端的多模态应用：

- **前端**：网页（手机浏览器打开），调用手机摄像头拍照 / 从相册选图，上传后展示识别结果。
- **后端**：FastAPI，接收图片，调用多模态大模型（默认 `deepseek-flash`，OpenAI 兼容接口）进行消防隐患分级。

## 风险等级

| 等级 | 判定标准 |
| --- | --- |
| 🔴 高风险 | 楼道中出现电动车、电瓶、飞线充电等可能起火的元素 |
| 🟠 中风险 | 大量堆积物严重影响通行，或堆放大量纸箱、木质家具等能造成火势蔓延的堵塞物 |
| 🟡 低风险 | 存在楼道堆物现象但不严重 |
| 🟢 无风险 | 楼道干净，无堆放物品 |
| ⚪ 非楼道 | 图片内容与楼道无关 |

## 目录结构

```
fire-safety-app/
├── app.py              # FastAPI 后端
├── requirements.txt
├── .env.example        # 配置模板（复制为 .env 填写）
└── static/
    └── index.html      # 前端页面（含相机调用 / 上传 / 结果展示）
```

## 快速开始

### 1. 配置

```bash
conda activate py312
cd fire-safety-app
pip install -r requirements.txt   # 环境里已有 fastapi/uvicorn/requests 可跳过

cp .env.example .env
# 编辑 .env，填入你的 API_KEY（以及可选的 MODEL_NAME / API_BASE_URL）
```

### 2. 启动后端

```bash
uvicorn app:app --host 0.0.0.0 --port 8000
```

浏览器打开 http://localhost:8000 即可在电脑上测试。

### 3. 手机访问（调用摄像头拍照）

手机需与本机在同一局域网，用本机局域网 IP 访问，例如：

```bash
# 查看本机局域网 IP（macOS）
ipconfig getifaddr en0
# 或在手机浏览器打开
# http://<本机IP>:8000
```

打开页面后点击「📷 拍照」即可调用手机后置摄像头；「🖼️ 从相册选择」可上传已有图片。

> 说明：拍照使用 `<input capture="environment">`，直接唤起手机原生相机，不依赖 HTTPS；
> 若希望网页内嵌实时摄像头（getUserMedia），在手机通过局域网 HTTP 访问时通常会被浏览器
> 因「非安全上下文」而禁用，因此这里以 `capture` 方式为主。

## 配置项

可通过环境变量或 `.env` 文件配置（环境变量优先级更高）：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `API_KEY` | 空 | 模型服务 API Key（必填，除非 `MOCK=1`） |
| `API_BASE_URL` | `https://api.deepseek.com` | OpenAI 兼容接口地址 |
| `MODEL_NAME` | `deepseek-flash` | 模型名称（可选 `deepseek-v4-pro`） |
| `MOCK` | `0` | 设为 `1` 使用本地模拟结果，便于无 Key 调试 |
| `TIMEOUT` | `60` | 模型调用超时秒数 |

## 接口说明

- `GET /` — 前端页面
- `GET /api/health` — 健康检查（返回当前模型 / 是否配置 Key / 是否 Mock）
- `POST /api/analyze` — 上传图片（`multipart/form-data`，字段名 `file`），返回：
  ```json
  {
    "risk": "低风险",
    "reason": "一句话判断依据",
    "raw": "模型原始输出",
    "level": { "id": "low", "color": "#fbc02d", "desc": "..." }
  }
  ```

## 无 API Key 时的本地调试

```bash
MOCK=1 uvicorn app:app --host 0.0.0.0 --port 8000
```

此时 `/api/analyze` 返回固定的模拟结果，可完整走通「拍照 → 上传 → 展示」流程。
