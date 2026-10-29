# 楼道消防安全隐患识别（前后端多模态应用）

参考《案例-多模态内容理解.ipynb》，实现一个带前后端的多模态应用：

- **前端**：网页调用手机摄像头拍照，或上传图片；
- **后端**：调用多模态大模型（DeepSeek Flash / Qwen-VL）识别楼道风险。

根据楼道是否存在堆积物、堆积物是否可燃、是否有起火风险，将隐患分为五类：

| 等级 | 含义 |
| --- | --- |
| 高风险 | 楼道中出现电动车、电瓶、飞线充电等可能起火的元素 |
| 中风险 | 大量堆积物严重影响通行，或堆放纸箱、木质家具等堵塞物 |
| 低风险 | 存在楼道堆物现象但不严重 |
| 无风险 | 楼道干净，无堆放物品 |
| 非楼道 | 与楼道无关的图片 |

## 目录结构

```
作业一/
├── app.py                # Flask 后端（页面 + /api/analyze 接口）
├── vision_client.py      # 多模态视觉客户端（DeepSeek / Qwen）
├── requirements.txt
├── .env.example          # 配置示例
├── templates/index.html  # 前端页面
└── static/
    ├── style.css
    └── app.js
```

## 快速开始

1. 安装依赖

   ```bash
   pip install -r requirements.txt
   ```

2. 配置 API Key（二选一）

   - DeepSeek：在 [platform.deepseek.com](https://platform.deepseek.com/api_keys) 申请 Key；
   - Qwen-VL：在 [阿里云百炼](https://bailian.console.aliyun.com) 申请 DashScope Key。

   通过环境变量设置（Linux/macOS 用 `export`，Windows CMD 用 `set`）：

   ```bash
   export VISION_PROVIDER=deepseek          # 或 qwen
   export DEEPSEEK_API_KEY=sk-xxxx          # DeepSeek Key
   # 或
   export DASHSCOPE_API_KEY=sk-xxxx         # Qwen Key
   ```

3. 启动

   ```bash
   python app.py
   ```

   浏览器打开 <http://localhost:8000>。

## 手机拍照（需要 HTTPS）

浏览器规定：调用摄像头 `getUserMedia` 必须在「安全上下文」（HTTPS 或 localhost）下进行。

- **电脑本地测试**：直接访问 `http://localhost:8000` 即可（localhost 属于安全上下文）。
- **手机真机拍照**：手机和电脑连同一 Wi-Fi，访问 `http://<电脑局域网IP>:8000`。
  但 `http://` 非安全上下文，摄像头会被禁用，可用以下任一方式：

  1. 启用自签名 HTTPS：
     ```bash
     FLASK_SSL=1 python app.py
     ```
     然后手机访问 `https://<电脑局域网IP>:8000`，浏览器首次会提示证书不安全，选择「继续访问」即可。
  2. 用内网穿透工具（如 `cloudflared tunnel`、`ngrok`）获得一个公网 HTTPS 地址。
  3. 不使用摄像头，改用「上传图片」按钮（上传不要求 HTTPS）。

## 接口说明

`POST /api/analyze`

- 请求方式一（JSON / base64，前端拍照走这里）：

  ```json
  { "image": "data:image/jpeg;base64,/9j/..." }
  ```

- 请求方式二（multipart 表单）：

  ```bash
  curl -F "image=@/path/to/photo.jpg" http://localhost:8000/api/analyze
  ```

- 响应：

  ```json
  {
    "risk_level": "高风险",
    "reason": "楼道内停放电动车，存在飞线充电。",
    "provider": "deepseek",
    "model": "deepseek-flash"
  }
  ```

## 说明

- `deepseek-flash` 是 DeepSeek 的多模态模型（旧名 `deepseek-v4-flash-vision-exp` 仍可用），
  模型名可通过环境变量 `DEEPSEEK_MODEL` 修改。
- Qwen 走阿里云百炼的 OpenAI 兼容模式，默认模型 `qwen-vl-plus`，可改为 `qwen-vl-max` 等。
- 识别结果仅供参考，请以专业消防检查意见为准。
