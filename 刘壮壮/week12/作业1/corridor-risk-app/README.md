# 楼安巡检

基于 Streamlit 和 DeepSeek V4.1 Flash 的楼道消防风险辅助识别 Demo。支持浏览器摄像头拍照和本地图片上传，并按固定业务规则输出：

- 高风险：电动车、电瓶、飞线充电等直接起火风险
- 中等风险：严重影响通行，或大量纸箱、木质家具等可燃堆物
- 低风险：存在少量楼道堆物
- 无隐患：楼道整洁、无堆物
- 非楼道：图片与楼道、楼梯间或疏散通道无关

## 运行环境

- Conda 环境 `py312`（Python 3.12）
- DeepSeek API Key

## 安装与配置

```bash
cd corridor-risk-app
conda activate py312
pip install -e ".[dev]"
cp .env.example .env
```

编辑 `.env`：

```dotenv
DEEPSEEK_API_KEY=你的_API_Key
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

也可以复制 `.streamlit/secrets.toml.example` 为 `.streamlit/secrets.toml` 并填写密钥。两个文件均已加入 `.gitignore`，不要把真实密钥提交到版本库。

## 启动

```bash
conda activate py312
streamlit run app.py
```

浏览器访问终端显示的 `http://localhost:8501`。`localhost` 属于浏览器安全上下文，可直接请求电脑摄像头权限。

手机访问时，摄像头组件通常要求 HTTPS。部署到 Streamlit Community Cloud 等 HTTPS 环境即可；如果在局域网内用电脑 IP 访问，需要在反向代理层配置 HTTPS。

## 测试

```bash
conda activate py312
pytest -q
```

测试不调用真实模型，不会产生 API 费用，覆盖五类风险优先级、图片格式校验、缩放转换和缺失密钥异常。

## 实现说明

1. `st.camera_input` 和 `st.file_uploader` 产生内存中的图片文件。
2. Pillow 校验图片、纠正 EXIF 方向，并将最长边压缩到 1600 像素。
3. `deepseek-flash` 以 JSON 模式提取楼道、高危元素、堆积程度、可燃物和通行影响。
4. `backend/risk_rules.py` 按确定性优先级计算最终风险，避免模型自由生成等级。
5. Session State 按图片哈希保存结果；更换图片会自动清除旧结果。

默认不把用户照片写入磁盘。模型结果仅供日常排查参考，不能替代消防专业检查。

## 目录

```text
corridor-risk-app/
├── app.py
├── backend/
│   ├── analyzer.py
│   ├── risk_rules.py
│   └── schemas.py
├── tests/
├── .streamlit/
│   ├── config.toml
│   └── secrets.toml.example
├── .env.example
└── pyproject.toml
```
