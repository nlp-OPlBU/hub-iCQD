"""单文档摄取编排：parse → chunk → index，每步推进状态机并记录一个 span。

状态只通过 ``_transition`` 修改，保证任何一次写入都经过状态机校验。
"""

from __future__ import annotations

from typing import Any

from app.core.config import PROJECT_ROOT, get_settings
from app.core.exceptions import ErrorCode
from app.core.logging import get_logger, log_event
from app.core.tracing import span
from app.domain.enums import DocStatus
from app.domain.state_machine import PROGRESS, ensure_transition
from app.infra.db import repo_document as repo
from app.infra.db.engine import session_scope
from app.infra.db.models import Document
from app.pipeline import index, mineru
from app.pipeline.chunking import split_markdown

logger = get_logger("app.pipeline.ingest")


def _transition(doc_id: str, target: DocStatus, **fields: Any) -> None:
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise LookupError(f"文档不存在: {doc_id}")
        ensure_transition(doc.status, target)
        doc.status = target.value
        doc.progress = PROGRESS[target]
        doc.stage = target.value
        for key, value in fields.items():
            setattr(doc, key, value)


def _prepare(doc_id: str) -> bool:
    """进入处理前复位状态；返回 True 表示已完成可跳过。"""
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            raise LookupError(f"文档不存在: {doc_id}")
        if doc.status == DocStatus.DONE.value:
            return True
        if doc.status != DocStatus.PENDING.value:
            # failed 或崩溃遗留的中间态，统一回到起点重跑
            ensure_transition(doc.status, DocStatus.PENDING)
            repo.update_document(
                session,
                doc_id,
                status=DocStatus.PENDING.value,
                stage=None,
                progress=0,
                error_code=None,
                error_message=None,
            )
        return False


def fail_document(doc_id: str, code: ErrorCode | str, message: str) -> None:
    """把文档标记为失败；已 done 的不覆盖，已 failed 的只更新错误信息。"""
    code_value = code.value if isinstance(code, ErrorCode) else code
    with session_scope() as session:
        doc = session.get(Document, doc_id)
        if doc is None:
            return
        if doc.status == DocStatus.DONE.value:
            return
        if doc.status == DocStatus.FAILED.value:
            repo.update_document(session, doc_id, error_code=code_value, error_message=message[:2000])
            return
        ensure_transition(doc.status, DocStatus.FAILED)
        doc.status = DocStatus.FAILED.value
        doc.progress = 100
        doc.stage = "failed"
        doc.error_code = code_value
        doc.error_message = message[:2000]


def process_document(doc_id: str) -> None:
    """完整摄取一篇文档；异常原样抛出，由 worker 决定重试或进 DLQ。"""
    settings = get_settings()
    with span("ingest", ref_id=doc_id):
        if _prepare(doc_id):
            log_event(logger, "ingest_skip_done", ref_id=doc_id)
            return

        pdf_path = settings.storage.raw / f"{doc_id}.pdf"
        output_dir = settings.storage.parsed / doc_id

        _transition(doc_id, DocStatus.PARSING)
        with span("ingest.parse", ref_id=doc_id):
            result = mineru.parse_document(doc_id, pdf_path, output_dir)
            if result.markdown_path is None:
                raise mineru.ParseError("MinerU 未产出 Markdown")
        _transition(
            doc_id,
            DocStatus.CHUNKING,
            md_path=str(result.markdown_path.relative_to(PROJECT_ROOT)),
            page_count=result.page_count,
            image_count=len(result.images),
        )

        with span("ingest.chunk", ref_id=doc_id):
            markdown = result.markdown_path.read_text(encoding="utf-8", errors="ignore")
            max_chars = settings.mineru.chunk_size
            overlap = settings.mineru.chunk_overlap
            # 有 content_list 时按页切块，让每个 chunk 带得回页码；否则整篇切、页码留空
            sources = result.pages or [(None, markdown)]
            raw_chunks = []
            for page_no, page_text in sources:
                raw_chunks.extend(
                    split_markdown(page_text, max_chars=max_chars, overlap=overlap, page_no=page_no)
                )
            chunk_rows = [
                {
                    "id": f"{doc_id}-{index:04d}",
                    "doc_id": doc_id,
                    "idx": index,
                    "kind": item.kind,
                    "page_no": item.page_no,
                    "text": item.text,
                }
                for index, item in enumerate(raw_chunks)
            ]
            with session_scope() as session:
                count = repo.replace_chunks(session, doc_id, chunk_rows)
                repo.update_document(session, doc_id, chunk_count=count)

        _transition(doc_id, DocStatus.INDEXING)
        with span("ingest.index", ref_id=doc_id):
            text_rows, image_rows = index.index_document(doc_id, chunk_rows, result.images)
            with session_scope() as session:
                repo.update_document(session, doc_id, chunk_count=text_rows, image_count=image_rows)

        _transition(doc_id, DocStatus.DONE)
        log_event(
            logger,
            "ingest_done",
            ref_id=doc_id,
            chunks=text_rows,
            images=image_rows,
            pages=result.page_count,
            paged=bool(result.pages),
        )
