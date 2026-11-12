"""
文档处理管线 - 将 PDF 解析 + 向量化串联成完整流程

被 Kafka 消费者调用
"""
from pathlib import Path
from typing import Dict

from loguru import logger

from ..config_loader import Config
from ..db import get_db
from ..models import DocumentStatus
from .pdf_parser import PDFParser
from .vector_store import VectorStore


class DocumentPipeline:
    """PDF → 解析 → 向量化 的完整管线"""

    def __init__(self):
        self.parser = PDFParser()
        self.vector_store = VectorStore()
        self.db = get_db()

    async def process(self, task_id: str, file_path: str, file_name: str):
        """
        处理单个 PDF 文档

        1. 更新状态为 parsing
        2. 调用 MinerU 解析
        3. 向量化存储
        4. 更新状态为 completed / error
        """
        logger.info(f"开始处理文档: task_id={task_id}, file={file_name}")

        cfg = Config.get_section('storage')
        parsed_dir = str(Path(cfg['parsed_dir']) / task_id)

        # Step 1: 标记为 parsing
        await self.db.update_status(task_id, DocumentStatus.PARSING)

        try:
            # Step 2: PDF 解析
            parse_result = self.parser.parse(file_path, parsed_dir)

            if not parse_result['success']:
                error_msg = parse_result.get('error', '未知解析错误')
                logger.error(f"解析失败: {error_msg}")
                await self.db.update_status(
                    task_id, DocumentStatus.ERROR, error_message=error_msg
                )
                return

            # Step 3: 向量化
            text = parse_result['text']
            images = parse_result.get('images', [])
            chunk_count = self.vector_store.add_document(
                task_id=task_id,
                file_name=file_name,
                text=text,
                images=images,
            )

            # Step 4: 更新状态为 completed
            preview = text[:500] + "..." if len(text) > 500 else text
            await self.db.update_status(
                task_id,
                DocumentStatus.COMPLETED,
                parsed_dir=parsed_dir,
                parsed_text_preview=preview,
            )

            logger.info(f"文档处理完成: task_id={task_id}, chunks={chunk_count}, images={len(images)}")

        except Exception as e:
            logger.error(f"文档处理异常: {e}")
            await self.db.update_status(
                task_id, DocumentStatus.ERROR, error_message=str(e)
            )


def run_pipeline_sync(message: Dict):
    """
    同步入口 - 被 Kafka Consumer 线程直接调用
    内部用 asyncio.run 执行异步管线
    """
    import asyncio

    task_id = message.get('task_id')
    file_path = message.get('file_path')
    file_name = message.get('file_name', Path(file_path).name if file_path else 'unknown')

    if not task_id or not file_path:
        logger.error(f"无效消息: {message}")
        return

    pipeline = DocumentPipeline()
    try:
        asyncio.run(pipeline.process(task_id, file_path, file_name))
    except Exception as e:
        logger.error(f"管线执行失败: {e}")
