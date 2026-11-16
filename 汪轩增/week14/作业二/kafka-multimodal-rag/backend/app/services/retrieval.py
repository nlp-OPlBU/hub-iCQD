"""多模态检索：BGE 文本召回 + CLIP 图/文召回，RRF 融合。"""

import logging

from ..config import Settings, get_settings
from .embedding import EmbeddingService
from .markdown_utils import IMAGE_MD_RE
from .media import ImageRef, UserImage
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


def fuse_rrf(rank_lists: list[list[dict]], k: int = 60) -> list[dict]:
    """Reciprocal Rank Fusion：按名次融合多路召回结果。"""
    merged: dict[int, dict] = {}
    for hits in rank_lists:
        for rank, hit in enumerate(hits):
            chunk_id = hit["chunk_id"]
            entry = merged.get(chunk_id)
            if entry is None:
                entry = {
                    "chunk_id": chunk_id,
                    "entity": hit["entity"],
                    "score": 0.0,
                }
                merged[chunk_id] = entry
            entry["score"] += 1.0 / (k + rank + 1)

    fused = sorted(merged.values(), key=lambda item: item["score"], reverse=True)
    if fused:
        best = fused[0]["score"] or 1.0
        for item in fused:
            item["score"] = round(item["score"] / best, 4)
    return fused


class Retriever:
    def __init__(
        self,
        embedder: EmbeddingService | None = None,
        store: VectorStore | None = None,
        settings: Settings | None = None,
    ):
        self.settings = settings or get_settings()
        self.embedder = embedder or EmbeddingService(self.settings)
        self.store = store or VectorStore(self.settings)

    def search(
        self,
        question: str,
        user_images: list[UserImage],
        top_k: int,
        doc_ids: list[int],
    ) -> list[dict]:
        recall_limit = max(top_k * 3, 10)
        rank_lists: list[list[dict]] = []

        # 1) 文本 -> BGE 文本向量召回
        bge_vector = self.embedder.encode_text_bge(question)
        rank_lists.append(
            self.store.search(bge_vector, "text_vector", recall_limit, doc_ids)
        )

        if user_images:
            # 2) 文本 -> CLIP 文本向量召回（跨模态对齐空间）
            clip_text_vector = self.embedder.encode_text_clip(question)
            rank_lists.append(
                self.store.search(
                    clip_text_vector, "clip_text_vector", recall_limit, doc_ids
                )
            )

            # 3) 用户图片 -> CLIP 图片向量召回“长得像/语义相关”的文档图片块
            for image in user_images[: self.settings.max_user_images]:
                image_vector = self.embedder.encode_image_pil(image.pil)
                image_hits = self.store.search(
                    image_vector, "clip_image_vector", recall_limit, doc_ids
                )
                # 没有图片的 chunk 存的是零向量，过滤掉
                rank_lists.append(
                    [hit for hit in image_hits if hit["entity"].get("image_paths")]
                )

        return fuse_rrf(rank_lists)[:top_k]


def build_context(hits: list[dict], settings: Settings) -> tuple[str, list[ImageRef]]:
    """把命中 chunk 拼成提示词上下文，并给其中的图片分配 [[IMG_n]] 占位符。"""
    prefix = settings.static_processed_prefix + "/"
    refs: list[ImageRef] = []
    ref_by_src: dict[str, ImageRef] = {}
    blocks: list[str] = []

    for number, hit in enumerate(hits, start=1):
        entity = hit["entity"]
        text = entity.get("text") or ""
        file_name = entity.get("file_name") or "未知文档"

        def _replace_image(match) -> str:
            src = match.group("src")
            if not src.startswith(prefix):
                return match.group(0)

            ref = ref_by_src.get(src)
            if ref is None:
                ref = ImageRef(
                    index=len(refs) + 1,
                    static_url=src,
                    absolute_url=settings.absolute_static_url(src),
                    source_file=file_name,
                    caption=match.group("alt") or "",
                )
                refs.append(ref)
                ref_by_src[src] = ref
            return f"[[IMG_{ref.index}]]"

        text_with_placeholders = IMAGE_MD_RE.sub(_replace_image, text).strip()
        blocks.append(f"[资料{number}] 来源: {file_name}\n{text_with_placeholders}")

    return "\n\n".join(blocks), refs
