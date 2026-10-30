# 楼道消防安全识别前端

使用 Streamlit 对接现有 `backend.py`，支持摄像头拍照、本地图片上传和图片 URL 三种输入方式，展示图片预览、风险等级及分析说明。文件支持 JPG、PNG、WEBP，最大 10 MB。

## 启动

在本目录打开终端，安装前端依赖：

```powershell
python -m pip install -r requirements-frontend.txt
```

后端需要在其 Python 环境中安装依赖并启动。已有环境可跳过安装：

```powershell
python -m pip install fastapi uvicorn python-multipart openai-agents Pillow
$env:DEEPSEEK_API_KEY = "你的模型 API Key"
$env:DeepSeek_URL = "你的模型服务地址"
python -m uvicorn backend:app --host 0.0.0.0 --port 8000
```

模型服务地址需支持现有 `my_agents.py` 使用的 Responses API 和图片输入，模型名称也需要与服务匹配。

另外打开一个终端，启动前端：

```powershell
python -m streamlit run frontend.py --server.address 0.0.0.0 --server.port 8501 --server.maxUploadSize 10
```

电脑打开 <http://localhost:8501>。默认后端地址为 `http://127.0.0.1:8000`，可在网页侧栏修改，也可在启动前设置：

```powershell
$env:BACKEND_URL = "http://127.0.0.1:8000"
```

## 手机使用

1. 手机与电脑连接同一 Wi-Fi，用 `http://电脑的局域网IP:8501` 打开页面；如无法访问，检查网络与防火墙设置。
2. 上传照片和输入图片 URL 可直接使用。网页实时调用手机摄像头需要浏览器摄像头权限和安全上下文：手机访问电脑的普通 HTTP 局域网地址通常无法使用摄像头。请通过可信 HTTPS 反向代理/隧道访问前端，并允许摄像头权限。电脑本机 `localhost` 通常可直接测试摄像头。[Streamlit 摄像头文档](https://docs.streamlit.io/develop/api-reference/widgets/st.camera_input)
3. 选择一种图片来源，拍照/上传或填写图片直链，确认预览后点击“开始识别”。

后端地址由 Streamlit 服务器访问，因此前后端同在电脑运行时，手机打开页面也无需把侧栏的 `127.0.0.1` 改成手机 IP。请求在 Python 服务间发送，无需配置浏览器 CORS。

## 接口约定

前端向 `POST /analyze_image` 发送以下任意一种请求，且每次只发送一种来源：

- 拍照或上传：`multipart/form-data`，文件字段为 `file`。
- 图片 URL：表单字段为 `image_url`，只接受 HTTP/HTTPS 地址。

预期响应与 `models.py` 一致：

```json
{
  "result": {
    "classification": "低风险",
    "description": "楼道内存在少量堆物。"
  }
}
```

支持 `高风险`、`中风险`、`低风险`、`无风险`、`非楼道` 五种结果。请求连接超时为 10 秒，等待响应超时为 120 秒。输入或后端地址改变时会清除旧结果，避免误把上一张图片的分析用于当前图片。

## 离线验证

在本目录运行：

```powershell
python -m unittest discover -s . -p test_frontend.py -v
```

测试使用模拟后端响应及 Streamlit AppTest，覆盖三种输入入口、接口字段、错误提示和切换图片清除结果。真实手机摄像头权限及模型识别需要在对应设备和已配置的后端上验证。

## 模型输出格式错误

`ModelBehaviorError: Invalid JSON when parsing model output` 表示模型回复未通过 SDK 对 `Result` 的校验；除了 JSON 语法错误，字段缺失、等级不在枚举内也会触发它。例如 `中等风险` 不符合当前模型要求的 `中风险`，带 Markdown 代码块的 JSON 也不能直接解析。

`my_agents.py` 已明确要求仅输出包含 `classification`、`description` 的 JSON。后端保留 `output_type=Result` 的校验，失败时返回 HTTP 502 和可读的错误说明。修改后需要重启后端；若依然失败，应核对模型服务对 Responses API、图片输入和 JSON Schema 的实际支持，不能仅凭 OpenAI 兼容地址就假设全部支持。

后端环境可离线验证错误处理：

```powershell
python -m unittest test_backend -v
```
