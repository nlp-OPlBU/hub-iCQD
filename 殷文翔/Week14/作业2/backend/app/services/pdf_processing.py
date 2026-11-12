"""Default PDF task processor: MinerU -> safe artifacts -> deterministic chunks -> ES."""
from __future__ import annotations

import logging
import shutil
from app.core.config import Settings
from app.db.base import utcnow
from app.repositories.task import TaskClaim
from app.services.chunking import build_chunks, read_markdown
from app.services.elasticsearch_store import ElasticsearchError, ElasticsearchStore
from app.services.mineru_cli import MineruError, MineruRunner, cleanup_workspace, safe_extract
from app.services.storage import FileStorage
from app.services.task_processing import TaskProcessingError, TaskResult

logger = logging.getLogger(__name__)


class MineruTaskProcessor:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.storage = FileStorage(settings)
        self.runner = MineruRunner(settings)
        self.index = ElasticsearchStore(settings)

    def process(self, claim: TaskClaim) -> TaskResult:
        pdf_path = self.storage.pdf_path(claim.document_id)
        if not pdf_path.is_file():
            raise TaskProcessingError("PDF_MISSING", "原始 PDF 不存在", retryable=False)
        workspace = self.settings.data_dir / "tmp" / "tasks" / claim.task_id
        artifact_dir = self.settings.data_dir / "parsed" / claim.document_id / f"v{claim.version}"
        zip_path = workspace / "result.zip"
        cleanup_workspace(workspace)
        if artifact_dir.exists():
            shutil.rmtree(artifact_dir, ignore_errors=True)
        try:
            self.runner.run(pdf_path, zip_path, workspace)
            safe_extract(zip_path, artifact_dir, self.settings)
            markdown = read_markdown(artifact_dir)
        except MineruError as exc:
            raise TaskProcessingError(exc.code, exc.message, retryable=exc.retryable) from exc
        except FileNotFoundError as exc:
            raise TaskProcessingError("ARTIFACT_EMPTY", "解析产物缺少 Markdown", retryable=False) from exc
        finally:
            cleanup_workspace(workspace)

        chunks = build_chunks(document_id=claim.document_id, version=claim.version,
                              filename=pdf_path.name, markdown=markdown)
        try:
            self.index.index_chunks(chunks)
        except ElasticsearchError as exc:
            raise TaskProcessingError("INDEX_UNAVAILABLE", "检索索引暂不可用") from exc

        artifact_key = artifact_dir.relative_to(self.settings.data_dir).as_posix()
        return TaskResult(artifact_key=artifact_key, index_name=self.settings.es_index,
                          indexed_at=utcnow(), retrieval_mode=claim.retrieval_mode)
