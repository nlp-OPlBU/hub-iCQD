"""一条命令拉起三个进程：进程1 → 进程2 → 后端。"""

from __future__ import annotations

import subprocess
import sys
import time
import urllib.error
import urllib.request

from config import BASE_DIR, settings

SERVICES = [
    ("进程1 情感识别", "sentiment_service:app", settings.sentiment_port),
    ("进程2 鼓励/排解", "response_service:app", settings.response_port),
    ("后端网关", "backend:app", settings.backend_port),
]


def wait_for_health(port: int, timeout: float = 20.0) -> None:
    """后启动的进程依赖前一个已就绪，避免首次打开页面时看到 degraded。"""
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/health"
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=1.0) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            pass
        time.sleep(0.3)
    raise RuntimeError(f"等待 {url} 就绪超时")


def main() -> int:
    processes: list[tuple[str, subprocess.Popen]] = []
    try:
        for name, target, port in SERVICES:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    target,
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=BASE_DIR,
            )
            processes.append((name, process))
            print(f"[启动] {name}  http://127.0.0.1:{port}")
            wait_for_health(port)

        print(f"\n打开 http://127.0.0.1:{settings.backend_port} 使用网页，"
              f"或运行 python cli.py 使用命令行。Ctrl+C 停止全部进程。\n")

        while True:
            time.sleep(1)
            for name, process in processes:
                code = process.poll()
                if code is not None:
                    print(f"[退出] {name} 已结束，退出码 {code}")
                    return code or 1
    except KeyboardInterrupt:
        print("\n正在停止进程……")
        return 0
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.terminate()
        for _, process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()


if __name__ == "__main__":
    sys.exit(main())
