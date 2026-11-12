"""
向量存储服务 - 基于 ChromaDB 实现

负责：
1. 将解析后的文本分块并向量化
2. 存储到向量数据库
3. 支持语义检索 (RAG)
"""
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from ..config_loader import Config


class VectorStore:
    """ChromaDB 向量存储封装"""

    def __init__(self):
        cfg = Config.get_section('vector_store')
        self.persist_dir = cfg.get('persist_dir', 'storage/chroma_db')
        self.collection_name = cfg.get('collection_name', 'rag_documents')
        self.chunk_size = cfg.get('chunk_size', 1000)
        self.chunk_overlap = cfg.get('chunk_overlap', 100)
        self.top_k = cfg.get('top_k', 5)

        self._client = None
        self._collection = None

    def _ensure_client(self):
        """初始化 ChromaDB 客户端和 Collection"""
        if self._client is not None:
            return
        try:
            import chromadb
            from chromadb.config import Settings
            self._client = chromadb.PersistentClient(
                path=self.persist_dir,
                settings=Settings(anonymized_telemetry=False)
            )
            self._collection = self._client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"ChromaDB 已连接: collection={self.collection_name}")
        except ImportError:
            logger.error("chromadb 未安装！请运行 pip install chromadb")
            raise

    def _chunk_text(self, text: str) -> List[str]:
        """简单的文本分块（按字符数 + 重叠）"""
        if not text or not text.strip():
            return []

        chunks: List[str] = []
        start = 0
        text_len = len(text)

        while start < text_len:
            end = start + self.chunk_size
            chunk = text[start:end]
            if chunk.strip():
                chunks.append(chunk)
            start = end - self.chunk_overlap

        return chunks

    def add_document(
        self,
        task_id: str,
        file_name: str,
        text: str,
        images: Optional[List[str]] = None,
    ) -> int:
        """
        将文档分块并添加到向量库

        Returns:
            添加的 chunk 数量
        """
        self._ensure_client()

        chunks = self._chunk_text(text)
        if not chunks:
            logger.warning(f"文档 {task_id} 无有效内容可向量化")
            return 0

        ids = [f"{task_id}_chunk_{i}" for i in range(len(chunks))]
        metadatas = [{
            "task_id": task_id,
            "file_name": file_name,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "images": ",".join(images) if images else "",
        } for i in range(len(chunks))]

        self._collection.add(
            ids=ids,
            documents=chunks,
            metadatas=metadatas,
        )

        logger.info(f"已向量化文档 {task_id}: {len(chunks)} chunks")
        return len(chunks)

    def query(
        self,
        query_text: str,
        top_k: Optional[int] = None,
    ) -> List[Dict]:
        """
        语义检索

        Returns:
            [{"content": str, "source": str, "task_id": str, "score": float}, ...]
        """
        self._ensure_client()

        k = top_k or self.top_k
        results = self._collection.query(
            query_texts=[query_text],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        items = []
        if results and results['documents'] and results['documents'][0]:
            docs = results['documents'][0]
            metas = results['metadatas'][0]
            distances = results['distances'][0] if results['distances'] else [0.0] * len(docs)

            for doc, meta, dist in zip(docs, metas, distances):
                items.append({
                    "content": doc,
                    "source": meta.get("file_name", "unknown"),
                    "task_id": meta.get("task_id", ""),
                    "score": 1.0 - dist,  # 距离转相似度
                })

        return items

    def delete_document(self, task_id: str):
        """删除某个文档的所有 chunks"""
        self._ensure_client()
        # ChromaDB 不支持按 metadata 删除，这里用 get 再 delete
        all_ids = self._collection.get(
            where={"task_id": task_id},
            include=[]
        )["ids"]
        if all_ids:
            self._collection.delete(ids=all_ids)
            logger.info(f"已删除向量库中文档 {task_id}")

    def count(self) -> int:
        """总文档块数"""
        self._ensure_client()
        return self._collection.count()

    def list_sources(self) -> List[str]:
        """列出所有源文件名"""
        self._ensure_client()
        data = self._collection.get(include=["metadatas"])
        sources = set()
        for meta in data['metadatas']:
            sources.add(meta.get('file_name', ''))
        return list(sources)
