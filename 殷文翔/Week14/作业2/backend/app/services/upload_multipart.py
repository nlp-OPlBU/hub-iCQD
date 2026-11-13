"""Stage and bound the entire HTTP body before processing any file transaction."""
from contextlib import asynccontextmanager
from tempfile import TemporaryFile

from fastapi import Request
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.errors import ApiError
from app.services.storage import CHUNK_BYTES


class CompleteMultiPartParser(MultiPartParser):
    ended = False

    def on_end(self):
        self.ended = True

    async def parse(self):
        try:
            form = await super().parse()
            if not self.ended:
                raise MultiPartException("Incomplete multipart body")
            return form
        except BaseException:
            # Starlette closes files only for MultiPartException. Also close them
            # on parser errors, storage errors and disconnected/cancelled requests.
            self.close_files()
            raise

    def close_files(self):
        for file in self._files_to_close_on_error:
            file.close()


@asynccontextmanager
async def staged_files(request: Request, *, max_files=None, max_bytes=None):
    settings = request.app.state.settings
    file_limit = max_files if max_files is not None else settings.upload_max_files
    byte_limit = min(max_bytes, settings.upload_max_request_bytes) if max_bytes is not None else settings.upload_max_request_bytes
    kind, _ = parse_options_header(request.headers.get("content-type", ""))
    if kind != b"multipart/form-data":
        raise ApiError(415, "INVALID_FILE_TYPE", "请使用 multipart/form-data 上传")
    length = request.headers.get("content-length")
    if length is not None:
        try:
            declared_size = int(length)
            if declared_size < 0:
                raise ValueError
        except ValueError:
            raise ApiError(400, "INVALID_MULTIPART", "请求长度不合法") from None
        if declared_size > byte_limit:
            raise ApiError(413, "REQUEST_TOO_LARGE", "请求超过总大小限制")
    temporary_dir = settings.data_dir / "tmp"
    await run_in_threadpool(temporary_dir.mkdir, parents=True, exist_ok=True)
    # Anonymous temporary body is automatically closed on every exit path.
    with TemporaryFile(dir=temporary_dir) as body:
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > byte_limit:
                raise ApiError(413, "REQUEST_TOO_LARGE", "请求超过总大小限制")
            await run_in_threadpool(body.write, chunk)
        await run_in_threadpool(body.seek, 0)

        async def replay():
            while chunk := await run_in_threadpool(body.read, CHUNK_BYTES):
                yield chunk

        parser = CompleteMultiPartParser(request.headers, replay(), max_files=file_limit,
                                         max_fields=0)
        try:
            try:
                form = await parser.parse()
            except MultiPartException as exc:
                if exc.message.startswith("Too many files."):
                    raise ApiError(422, "FILE_COUNT_EXCEEDED", "文件数量超过限制",
                                   details={"max_files": file_limit}) from None
                raise ApiError(400, "INVALID_MULTIPART", "上传表单不完整或格式错误") from None
            except (MultipartParseError, ValueError, KeyError):
                raise ApiError(400, "INVALID_MULTIPART", "上传表单格式错误") from None
            items = form.multi_items()
            if not items:
                raise ApiError(422, "VALIDATION_ERROR", "至少上传一个文件")
            if any(name != "files" or not isinstance(file, UploadFile) for name, file in items):
                raise ApiError(422, "VALIDATION_ERROR", "仅接受 files 文件字段")
            yield [file for _, file in items]
        finally:
            parser.close_files()
