"""Foreground supervisor for project API/dispatcher/worker; never starts infrastructure."""
import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--nginx-config', type=Path)
    parser.add_argument('--shutdown-seconds', type=int, default=60)
    args = parser.parse_args()
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    children = []
    commands = [[sys.executable, '-m', module] for module in (
        'app.main', 'app.workers.outbox_dispatcher', 'app.workers.pdf_consumer')]
    if args.nginx_config:
        config = args.nginx_config
        config = (ROOT / config).resolve(strict=True) if not config.is_absolute() else config.resolve(strict=True)
        command = ['nginx', '-p', str(config.parent) + '/', '-c', str(config)]
        subprocess.run([*command, '-t'], check=True)
        commands.append([*command, '-g', 'daemon off;'])
    code = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command, cwd=ROOT / 'backend', start_new_session=True))
        while not stop.wait(1):
            if any(child.poll() is not None for child in children):
                code = 1
                break
    finally:
        # Stop the API first, let the worker persist its current result before force killing.
        for child in children:
            if child.poll() is None:
                child.send_signal(signal.SIGTERM)
        import time
        deadline = time.monotonic() + max(1, args.shutdown_seconds)
        for child in children:
            try:
                child.wait(timeout=max(.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                code = 1
        # MinerU creates its own session. Its Linux parent-death protection handles a
        # force-killed worker; normal CLI timeouts kill the whole CLI process group.
        for child in children:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
    return code


if __name__ == '__main__':
    raise SystemExit(main())
