#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Week12 作业一 启动入口：启动 FastAPI 服务并提供网页。

    python run.py                 # 默认 0.0.0.0:8000
    python run.py --port 9000     # 换端口

手机访问：与电脑连同一 Wi-Fi，浏览器打开 http://<电脑局域网IP>:<端口>
（http 下用"拍摄/上传"按钮会直接唤起系统相机）。
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parent / ".env")


def main() -> None:
    parser = argparse.ArgumentParser(description="楼道消防隐患识别服务")
    parser.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="开发模式自动重载")
    args = parser.parse_args()

    import uvicorn

    uvicorn.run("server.main:app", host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
