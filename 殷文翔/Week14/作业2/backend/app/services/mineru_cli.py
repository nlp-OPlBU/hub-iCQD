"""MinerU CLI adapter with per-task workspaces, timeout and safe ZIP extraction."""
from __future__ import annotations

import logging
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import zipfile

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
                if (not name or name.startswith("/") or ":" in path.parts[0]
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
    except (zipfile.BadZipFile, OSError) as exc:
        if isinstance(exc, MineruError):
            raise
        raise MineruError("INVALID_ZIP", "解析产物不是有效 ZIP", retryable=False) from exc


class MineruRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def run(self, pdf_path: Path, output_zip: Path, workspace: Path) -> None:
        command = [
            self.settings.mineru_command, "parse", str(pdf_path),
            "--format", "zip", "-o", str(output_zip), "--tier", self.settings.mineru_tier,
        ]
        env = os.environ.copy()
        env["MINERU_HOME"] = str(self.settings.mineru_home)
        env["MINERU_CONFIG"] = str(self.settings.mineru_config)
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            completed = subprocess.run(
                command, cwd=self.settings.mineru_working_dir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
                timeout=self.settings.mineru_timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise MineruError("MINERU_TIMEOUT", "MinerU 解析超时") from exc
        except OSError as exc:
            raise MineruError("MINERU_UNAVAILABLE", "无法启动 MinerU CLI") from exc
        if completed.returncode != 0:
            logger.warning("MinerU exited code=%s", completed.returncode)
            raise MineruError("MINERU_FAILED", "MinerU 解析失败")
        if not output_zip.is_file() or output_zip.stat().st_size == 0:
            raise MineruError("MINERU_NO_OUTPUT", "MinerU 未生成解析产物")


def cleanup_workspace(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
