"""
RAG 服务 - 整合向量检索 + LLM 生成，提供多模态问答能力
"""
from typing import Dict, List, Optional

from loguru import logger

from ..config_loader import Config
from .llm_service import LLMService
from .vector_store import VectorStore


class RAGService:
    """RAG 问答服务"""

    def __init__(self):
        cfg = Config.get_section('prompt')
        self.system_prompt_template = cfg.get('system_prompt', '')
        self.chat_prompt = cfg.get('chat_prompt', '你是一个智能问答助手。')

        self.llm = LLMService()
        self.vector_store = VectorStore()

    def _build_rag_prompt(self, question: str, context_docs: List[Dict]) -> str:
        """构建 RAG prompt"""
        if not context_docs:
            return self.chat_prompt + f"\n\n用户问题：{question}"

        context_parts = []
        for i, doc in enumerate(context_docs, 1):
            context_parts.append(f"【来源{i}: {doc['source']}】\n{doc['content']}")
        context_str = "\n\n---\n\n".join(context_parts)

        prompt = self.system_prompt_template.format(
            context=context_str,
            question=question,
        )
        return prompt

    async def answer(
        self,
        question: str,
        images: Optional[List[str]] = None,
        use_rag: bool = True,
        history: Optional[List[Dict]] = None,
    ) -> Dict:
        """多模态问答主入口"""
        sources = []
        rag_prompt = self.chat_prompt

        if use_rag:
            try:
                sources = self.vector_store.query(question)
                logger.info(f"RAG 检索到 {len(sources)} 条相关内容")
                rag_prompt = self._build_rag_prompt(question, sources)
            except Exception as e:
                logger.warning(f"RAG 检索失败，降级为纯问答: {e}")
                rag_prompt = self.chat_prompt + f"\n\n用户问题：{question}"

        try:
            if images:
                logger.info(f"使用 Vision 模型处理问题 + {len(images)} 张图片")
                answer = await self.llm.chat_multimodal(
                    system_prompt=rag_prompt,
                    user_question=question,
                    images=images,
                    history=history,
                )
            else:
                answer = await self.llm.chat_text(
                    system_prompt=rag_prompt,
                    user_question=question,
                    history=history,
                )

            has_images = '![' in answer and '](' in answer

            return {
                "answer": answer,
                "sources": sources,
                "has_images_in_answer": has_images,
            }

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            fallback = "抱歉，LLM 服务暂时不可用。以下是检索到的相关内容：\n\n"
            if sources:
                for s in sources:
                    fallback += f"**来源: {s['source']}**\n{s['content'][:200]}...\n\n"
            else:
                fallback += "（暂无相关文档）"

            return {
                "answer": fallback,
                "sources": sources,
                "has_images_in_answer": False,
                "error": str(e),
            }
