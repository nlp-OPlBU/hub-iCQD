"""把解析后的 markdown 向量化并写入 Milvus。"""

import logging
from pathlib import Path

from ..config import Settings, get_settings
from .chunker import split_markdown
from .embedding import EmbeddingService
from .markdown_utils import IMAGE_MD_RE, extract_image_srcs, rewrite_local_images, strip_images
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class DocumentIndexer:
    def __init__(
        self,
        embedder: EmbeddingService | None = None,
        store: VectorStore | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.embedder = embedder or EmbeddingService(self.settings)
        self.store = store or VectorStore(self.settings)

    def index_document(
        self,
        document_id: int,
        markdown_path: str | Path,
        file_name: str,
    ) -> int:
        """切块 + 编码 + 入库，返回 chunk 数。重复处理时会先删旧向量（幂等）。"""
        markdown_path = Path(markdown_path)
        raw = markdown_path.read_text(encoding="utf-8", errors="ignore")

        # 图片相对路径 -> /static/processed/...，前端可直接访问
        markdown = rewrite_local_images(raw, markdown_path.parent, self.settings)

        chunks = split_markdown(markdown, max_chars=self.settings.chunk_size)
        if not chunks:
            raise ValueError("解析结果为空，未生成任何 chunk")

        # 幂等：Kafka 至少一次投递 / 手动重试时，先清掉旧数据
        self.store.delete_by_doc(document_id)

        static_prefix = self.settings.static_processed_prefix + "/"
        zero_image_vector = self.embedder.zero_image_vector()

        records: list[dict] = []
        for index, chunk in enumerate(chunks):
            text_for_embedding = strip_images(chunk)
            if not text_for_embedding:
                # 纯图片块：用 alt 文本兜底编码
                alts = [m.group("alt") for m in IMAGE_MD_RE.finditer(chunk)]
                text_for_embedding = " ".join(a for a in alts if a) or "图片"

            text_vector = self.embedder.encode_text_bge(text_for_embedding)
            clip_text_vector = self.embedder.encode_text_clip(text_for_embedding)

            image_urls = [
                src for src in extract_image_srcs(chunk) if src.startswith(static_prefix)
            ]
            clip_image_vector = zero_image_vector
            for src in image_urls:
                local_path = self.settings.static_url_to_local(src)
                if local_path is None:
                    continue
                try:
                    # 每块只用第一张图做图片向量
                    clip_image_vector = self.embedder.encode_image_path(local_path)
                    break
                except Exception:
                    logger.exception("图片向量化失败: %s", local_path)

            records.append(
                {
                    "doc_id": int(document_id),
                    "chunk_index": index,
                    "file_name": file_name,
                    "text": chunk,
                    "image_paths": image_urls,
                    "text_vector": text_vector,
                    "clip_text_vector": clip_text_vector,
                    "clip_image_vector": clip_image_vector,
                }
            )

        self.store.insert(records)
        logger.info(
            "Document %s indexed: %s chunks, %s images",
            document_id,
            len(records),
            sum(1 for r in records if r["image_paths"]),
        )
        return len(records)
