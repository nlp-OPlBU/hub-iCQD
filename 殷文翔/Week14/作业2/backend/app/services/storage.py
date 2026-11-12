"""Bounded PDF storage with durable receipts for filesystem/DB reconciliation."""
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import re
from typing import BinaryIO
from uuid import UUID

from app.api.errors import ApiError
from app.core.config import Settings

logger = logging.getLogger(__name__)
CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True)
class SavedPDF:
    document_id: str
    task_id: str
    storage_key: str
    filename: str
    size_bytes: int
    sha256: str


class FileStorage:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.data_dir
        self.recovery_dir = self.root / "tmp" / "upload-recovery"

    def pdf_path(self, document_id: str) -> Path:
        # Only server-generated UUIDs can select filesystem paths.
        document_id = str(UUID(document_id))
        path = (self.root / "uploads" / f"{document_id}.pdf").resolve()
        path.relative_to(self.root.resolve())
        return path

    def receipt_path(self, document_id: str) -> Path:
        return self.recovery_dir / f"{UUID(document_id)}.json"

    def save_pdf(self, source: BinaryIO, filename: str, media_type: str | None,
                 document_id: str, task_id: str) -> SavedPDF:
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise ApiError(422, "INVALID_FILENAME", "文件名为空、过长或包含控制字符")
        if not filename.lower().endswith(".pdf") or media_type not in ("application/pdf", "application/octet-stream"):
            raise ApiError(415, "INVALID_FILE_TYPE", "仅允许 PDF 文件")
        source.seek(0, os.SEEK_END)
        size = source.tell()
        if size > self.settings.upload_max_file_bytes:
            raise ApiError(413, "FILE_TOO_LARGE", "文件超过大小限制",
                           details={"max_bytes": self.settings.upload_max_file_bytes})
        if not size:
            raise ApiError(422, "EMPTY_FILE", "文件不能为空")
        source.seek(0)
        header = source.read(16)
        source.seek(max(0, size - 1024))
        tail = source.read(1024).rstrip(b" \t\r\n\x00")
        # Container sanity only. PDF parsing belongs exclusively to the worker.
        if not re.match(rb"%PDF-(?:1\.[0-7]|2\.0)[\r\n \t]", header) or not tail.endswith(b"%%EOF"):
            raise ApiError(415, "INVALID_FILE_TYPE", "PDF 文件头或结束标记不合法")
        key = f"uploads/{document_id}.pdf"
        receipt = {"document_id": document_id, "task_id": task_id, "storage_key": key}
        path = self.pdf_path(document_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.recovery_dir.mkdir(parents=True, exist_ok=True)
        # Write and fsync the receipt BEFORE the final file, so an interrupted write
        # or a lost COMMIT acknowledgement always has a discoverable recovery record.
        with self.receipt_path(document_id).open("x", encoding="utf-8") as record:
            json.dump(receipt, record)
            record.flush()
            os.fsync(record.fileno())
        digest = hashlib.sha256()
        source.seek(0)
        copied = 0
        with path.open("xb") as target:
            while chunk := source.read(CHUNK_BYTES):
                copied += len(chunk)
                if copied > self.settings.upload_max_file_bytes:
                    raise ApiError(413, "FILE_TOO_LARGE", "文件超过大小限制")
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        if copied != size:
            raise OSError("Upload size changed during persistence")
        return SavedPDF(document_id, task_id, key, filename, copied, digest.hexdigest())

    def forget_receipt(self, document_id: str) -> bool:
        try:
            self.receipt_path(document_id).unlink(missing_ok=True)
            return True
        except OSError:
            logger.warning("Upload receipt cleanup deferred document_id=%s", document_id)
            return False

    def discard_uncommitted(self, document_id: str) -> bool:
        try:
            self.pdf_path(document_id).unlink(missing_ok=True)
        except OSError:
            logger.warning("Upload file cleanup deferred document_id=%s", document_id)
            return False  # Keep the receipt until the file can actually be removed.
        return self.forget_receipt(document_id)
