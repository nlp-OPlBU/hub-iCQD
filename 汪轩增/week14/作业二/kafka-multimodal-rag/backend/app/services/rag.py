"""RAG 主流程：检索 -> 拼上下文 -> 多模态大模型 -> 图文混排答案。"""

import logging
import re

from ..config import Settings, get_settings
from ..schemas import ChatResponse, SourceItem
from .embedding import EmbeddingService
from .llm import LLMService, render_answer
from .markdown_utils import strip_images
from .media import UserImage
from .retrieval import Retriever, build_context
from .vector_store import VectorStore

logger = logging.getLogger(__name__)


class RAGService:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        embedder = EmbeddingService(self.settings)
        store = VectorStore(self.settings)
        self.retriever = Retriever(embedder=embedder, store=store, settings=self.settings)
        self.llm = LLMService(self.settings)

    def answer(
        self,
        question: str,
        user_images: list[UserImage],
        top_k: int,
        doc_ids: list[int],
    ) -> ChatResponse:
        hits = self.retriever.search(question, user_images, top_k, doc_ids)
        if not hits:
            return ChatResponse(
                answer="未在当前知识库中检索到与问题相关的内容。可以换个问法，或先上传并等待文档解析完成。",
                sources=[],
                images=[],
            )

        context, image_refs = build_context(hits, self.settings)

        raw_answer = self.llm.answer(
            question=question,
            context=context,
            image_refs=image_refs,
            user_images=user_images,
        )
        answer = render_answer(raw_answer, image_refs)

        sources: list[SourceItem] = []
        for hit in hits:
            entity = hit["entity"]
            snippet = re.sub(r"\s+", " ", strip_images(entity.get("text", ""))).strip()
            if len(snippet) > 180:
                snippet = snippet[:180] + "…"
            if not snippet:
                snippet = "[图片内容]"
            image_urls = [
                self.settings.absolute_static_url(src)
                for src in (entity.get("image_paths") or [])
            ]
            sources.append(
                SourceItem(
                    doc_id=int(entity.get("doc_id", -1)),
                    file_name=entity.get("file_name", "未知文档"),
                    snippet=snippet,
                    score=float(hit.get("score", 0.0)),
                    image_urls=image_urls,
                )
            )

        return ChatResponse(
            answer=answer,
            sources=sources,
            images=[ref.absolute_url for ref in image_refs],
        )
