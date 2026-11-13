"""MinerU CLI adapter with per-task workspaces, timeout and safe ZIP extraction."""
from __future__ import annotations

from contextlib import contextmanager
import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import signal
import sys
import time
import zipfile
import zlib

from app.core.config import Settings

logger = logging.getLogger(__name__)
COPY_BYTES = 1024 * 1024


class MineruError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = True):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


def safe_extract(zip_path: Path, destination: Path, settings: Settings) -> None:
    """Extract a ZIP using declared and actual limits; reject traversal and links."""
    destination = destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    total = 0
    written_total = 0
    try:
        with zipfile.ZipFile(zip_path) as archive:
            infos = archive.infolist()
            if len(infos) > settings.zip_max_entries:
                raise MineruError("ZIP_TOO_MANY_ENTRIES", "解析产物归档条目过多", retryable=False)
            for info in infos:
                name = info.filename.replace("\\", "/")
                path = PurePosixPath(name)
                if (not path.parts or not name or name.startswith("/") or ":" in path.parts[0]
                        or any(part in ("", ".", "..") for part in path.parts)):
                    raise MineruError("ZIP_UNSAFE_PATH", "解析产物包含不安全路径", retryable=False)
                if stat.S_ISLNK((info.external_attr >> 16) & 0o170000):
                    raise MineruError("ZIP_UNSAFE_LINK", "解析产物包含符号链接", retryable=False)
                if info.is_dir():
                    continue
                if info.file_size > settings.zip_max_entry_bytes:
                    raise MineruError("ZIP_ENTRY_TOO_LARGE", "解析产物单文件过大", retryable=False)
                if info.file_size and (not info.compress_size or
                                       info.file_size / info.compress_size > settings.zip_max_compression_ratio):
                    raise MineruError("ZIP_SUSPICIOUS_RATIO", "解析产物压缩比异常", retryable=False)
                total += info.file_size
                if total > settings.zip_max_uncompressed_bytes:
                    raise MineruError("ZIP_TOO_LARGE", "解析产物解压后总大小超限", retryable=False)
                target = (destination / Path(*path.parts)).resolve()
                target.relative_to(destination)
                target.parent.mkdir(parents=True, exist_ok=True)
                actual = 0
                with archive.open(info) as source, target.open("xb") as output:
                    while chunk := source.read(COPY_BYTES):
                        actual += len(chunk)
                        written_total += len(chunk)
                        if actual > settings.zip_max_entry_bytes or written_total > settings.zip_max_uncompressed_bytes:
                            raise MineruError("ZIP_TOO_LARGE", "解析产物实际解压大小超限", retryable=False)
                        output.write(chunk)
                if actual != info.file_size:
                    raise MineruError("ZIP_SIZE_MISMATCH", "解析产物声明大小与实际不符", retryable=False)
    except (zipfile.BadZipFile, OSError, RuntimeError, NotImplementedError, EOFError, zlib.error) as exc:
        if isinstance(exc, MineruError):
            raise
        raise MineruError("INVALID_ZIP", "解析产物不是有效 ZIP", retryable=False) from exc


class MineruRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    @contextmanager
    def _slot(self):
        # OS locks release on process exit; slots are shared across local workers.
        import fcntl
        root = self.settings.data_dir / "tmp" / "mineru-slots"
        root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.settings.mineru_timeout_seconds
        slot = None
        while slot is None:
            for number in range(self.settings.mineru_max_concurrency):
                handle = (root / f"{number}.lock").open("a+b")
                try:
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    slot = handle
                    break
                except BlockingIOError:
                    handle.close()
            if slot is None:
                if time.monotonic() >= deadline:
                    raise MineruError("MINERU_BUSY", "等待 MinerU 并发名额超时")
                time.sleep(0.1)
        try:
            yield
        finally:
            slot.close()

    def run(self, pdf_path: Path, output_zip: Path, workspace: Path) -> None:
        command = [
            self.settings.mineru_command, "parse", str(pdf_path.resolve()),
            "--format", "zip", "-o", str(output_zip.resolve()), "--tier", self.settings.mineru_tier,
        ]
        if sys.platform == 'linux':
            command = [sys.executable, str(Path(__file__).with_name('mineru_subprocess.py')),
                       str(os.getpid()), *command]
        env = os.environ.copy()
        env["MINERU_HOME"] = str(self.settings.mineru_home)
        env["MINERU_CONFIG"] = str(self.settings.mineru_config)
        if self.settings.mineru_device_mode:
            env['MINERU_DEVICE_MODE'] = self.settings.mineru_device_mode
        if self.settings.mineru_offline:
            env['HF_HUB_OFFLINE'] = '1'
        for variable in ('OMP_NUM_THREADS', 'MKL_NUM_THREADS'):
            env.setdefault(variable, str(self.settings.mineru_cpu_threads))
        workspace.mkdir(parents=True, exist_ok=True)
        with self._slot():
            try:
                # Files avoid buffering unbounded CLI output in worker memory.
                with (workspace / "stdout.log").open("wb") as stdout, (workspace / "stderr.log").open("wb") as stderr:
                    process = subprocess.Popen(command, cwd=workspace, env=env,
                                               stdout=stdout, stderr=stderr, start_new_session=True)
                    try:
                        returncode = process.wait(timeout=self.settings.mineru_timeout_seconds)
                    except subprocess.TimeoutExpired as exc:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        process.wait()
                        raise MineruError("MINERU_TIMEOUT", "MinerU 解析超时") from exc
            except OSError as exc:
                raise MineruError("MINERU_UNAVAILABLE", "无法启动 MinerU CLI") from exc
            if returncode != 0:
                logger.warning("MinerU exited code=%s", returncode)
                raise MineruError("MINERU_FAILED", f"MinerU 解析失败，退出码 {returncode}")
            if not output_zip.is_file() or output_zip.stat().st_size == 0:
                raise MineruError("MINERU_NO_OUTPUT", "MinerU 未生成解析产物")


def cleanup_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
