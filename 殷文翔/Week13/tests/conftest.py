"""离线测试：在导入模块级模型前设置测试配置。"""

import os
import sys
from pathlib import Path


# 项目采用 src 布局且没有安装包配置，让 pytest 能直接导入当前工作区源码。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
# Agent 在模块导入时创建模型对象，需要提前提供占位配置；实际请求由 AsyncMock 替代。
# 使用非真实密钥和本机地址，避免测试误用开发者配置的模型服务。
os.environ["DEEPSEEK_API_KEY"] = "test-only-not-a-real-key"
os.environ["DEEPSEEK_URL"] = "http://127.0.0.1:9"
# 禁用新旧命名的追踪开关，避免离线测试向外部追踪服务发送运行信息。
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
