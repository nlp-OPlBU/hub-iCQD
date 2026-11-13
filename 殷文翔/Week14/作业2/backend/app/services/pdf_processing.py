"""MinerU -> validated durable artifacts -> searchable BM25 chunks -> task result."""
from __future__ import annotations

import json
import os

from app.core.config import Settings
from app.db.base import utcnow
from app.repositories.task import TaskClaim
from app.services.chunking import build_chunks
from app.services.elasticsearch_store import ElasticsearchError, ElasticsearchStore
from app.services.mineru_artifacts import load_artifacts
from app.services.mineru_cli import MineruError, MineruRunner, cleanup_workspace, safe_extract
from app.services.storage import FileStorage
from app.services.task_processing import TaskProcessingError, TaskResult


class MineruTaskProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = FileStorage(settings)
        self.runner = MineruRunner(settings)
        self.index = ElasticsearchStore(settings)

    def process(self, claim: TaskClaim) -> TaskResult:
        if claim.retrieval_mode != 'bm25':
            raise TaskProcessingError('RETRIEVAL_UNSUPPORTED', '当前解析仅支持 BM25', retryable=False)
        pdf_path = self.storage.pdf_path(claim.document_id)
        if not pdf_path.is_file():
            raise TaskProcessingError('PDF_MISSING', '原始 PDF 不存在', retryable=False)
        # Each lease owns its artifacts; an expired worker cannot delete a successor's files.
        workspace = self.settings.data_dir / 'tmp' / 'tasks' / claim.task_id / claim.lease_token
        artifact_dir = self.settings.data_dir / 'parsed' / claim.document_id / f'v{claim.version}' / claim.lease_token
        artifact_key = artifact_dir.relative_to(self.settings.data_dir).as_posix()
        zip_path = workspace / 'result.zip'
        stage = 'parsing'
        try:
            self.runner.run(pdf_path, zip_path, workspace)
            stage = 'validating'
            extracted = workspace / 'extracted'
            safe_extract(zip_path, extracted, self.settings)
            artifact = load_artifacts(extracted, artifact_key)
            chunks = build_chunks(document_id=claim.document_id, version=claim.version,
                                  filename=claim.original_filename or pdf_path.name, artifact=artifact)
            with (extracted / 'normalized.json').open('x', encoding='utf-8') as output:
                json.dump(artifact, output, ensure_ascii=False)
            stage = 'persisting'
            # Flush all extracted files before publishing the immutable attempt directory.
            for path in extracted.rglob('*'):
                if path.is_file():
                    with path.open('rb') as handle:
                        os.fsync(handle.fileno())
            # Persist directory entries too, so a completed task survives a host crash.
            for directory in [p for p in extracted.rglob("*") if p.is_dir()] + [extracted]:
                _sync_directory(directory)
            artifact_dir.parent.mkdir(parents=True, exist_ok=True)
            extracted.rename(artifact_dir)
            for directory in (artifact_dir.parent, *artifact_dir.parent.parents):
                _sync_directory(directory)
                if directory == self.settings.data_dir:
                    break
            stage = 'indexing'
            self.index.index_chunks(chunks)
        except MineruError as exc:
            raise TaskProcessingError(exc.code, exc.message, retryable=exc.retryable,
                                      details={'stage': stage, 'attempt': claim.attempts}) from exc
        except ElasticsearchError as exc:
            raise TaskProcessingError('INDEX_UNAVAILABLE', 'ES 写入或可检索验证失败',
                                      details={'stage': stage, 'artifact_key': artifact_key}) from exc
        except OSError as exc:
            raise TaskProcessingError('ARTIFACT_IO_ERROR', '解析产物读写失败', details={'stage': stage}) from exc
        finally:
            cleanup_workspace(workspace)
        return TaskResult(artifact_key=artifact_key, index_name=self.settings.es_index,
                          indexed_at=utcnow(), retrieval_mode='bm25')


def _sync_directory(path):
    if os.name == 'posix':
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
