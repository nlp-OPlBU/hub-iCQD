"""容器入口：拉起三个 uvicorn 进程，并在收到停止信号时优雅退出。

进程1 8201、进程2 8202 只绑 127.0.0.1；后端 8200 对外。
"""

from __future__ import annotations

import signal
import subprocess
import sys
import time

SERVICES = [
    ("sentiment", ["-m", "uvicorn", "sentiment_service:app", "--host", "127.0.0.1", "--port", "8201"]),
    ("response", ["-m", "uvicorn", "response_service:app", "--host", "127.0.0.1", "--port", "8202"]),
    ("backend", ["-m", "uvicorn", "backend:app", "--host", "0.0.0.0", "--port", "8200"]),
]


def spawn(args: list[str]) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, *args], cwd="/app")


def main() -> int:
    processes = [(name, spawn(args)) for name, args in SERVICES]
    for name, process in processes:
        print(f"[launcher] {name} started, pid={process.pid}", flush=True)

    stopping = False

    def request_stop(signum: int, _frame) -> None:
        nonlocal stopping
        print(f"[launcher] received signal {signum}, stopping", flush=True)
        stopping = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)

    while not stopping:
        time.sleep(0.5)
        for name, process in processes:
            code = process.poll()
            if code is not None:
                print(f"[launcher] {name} exited with code {code}, stopping", flush=True)
                stopping = True
                break

    for _, process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.time() + 5
    for _, process in processes:
        try:
            process.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            process.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
