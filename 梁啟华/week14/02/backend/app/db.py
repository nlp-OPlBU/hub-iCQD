"""
数据库层 - SQLite（使用 aiosqlite 异步访问）

存储上传文档的元信息和处理状态。
"""
import json
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Dict, List, Optional

import aiosqlite
from loguru import logger

from .config_loader import Config
from .models import DocumentStatus


class Database:
    """SQLite 数据库封装"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """异步连接上下文管理器"""
        async with aiosqlite.connect(self.db_path) as conn:
            conn.row_factory = aiosqlite.Row
            yield conn

    async def init(self):
        """初始化数据库表"""
        async with self.connect() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS documents (
                    task_id TEXT PRIMARY KEY,
                    file_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    file_size INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    error_message TEXT,
                    parsed_dir TEXT,
                    parsed_text_preview TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
            """)
            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status)
            """)
            await conn.commit()
            logger.info("数据库初始化完成")

    # -------- 写入方法 --------

    async def create_document(
        self,
        task_id: str,
        file_name: str,
        file_path: str,
        file_size: int,
    ) -> None:
        """创建文档记录（状态: pending）"""
        now = datetime.now().isoformat()
        async with self.connect() as conn:
            await conn.execute(
                """
                INSERT INTO documents
                (task_id, file_name, file_path, file_size, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (task_id, file_name, file_path, file_size, DocumentStatus.PENDING.value, now, now)
            )
            await conn.commit()

    async def update_status(
        self,
        task_id: str,
        status: DocumentStatus,
        error_message: Optional[str] = None,
        parsed_dir: Optional[str] = None,
        parsed_text_preview: Optional[str] = None,
    ) -> None:
        """更新文档状态"""
        now = datetime.now().isoformat()
        updates = ["status = ?", "updated_at = ?"]
        values: List = [status.value, now]

        if error_message is not None:
            updates.append("error_message = ?")
            values.append(error_message)
        if parsed_dir is not None:
            updates.append("parsed_dir = ?")
            values.append(parsed_dir)
        if parsed_text_preview is not None:
            updates.append("parsed_text_preview = ?")
            values.append(parsed_text_preview)

        values.append(task_id)
        async with self.connect() as conn:
            await conn.execute(
                f"UPDATE documents SET {', '.join(updates)} WHERE task_id = ?",
                values
            )
            await conn.commit()

    # -------- 查询方法 --------

    async def get_document(self, task_id: str) -> Optional[Dict]:
        """按 task_id 查询单个文档"""
        async with self.connect() as conn:
            async with conn.execute(
                "SELECT * FROM documents WHERE task_id = ?", (task_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def list_documents(self, status: Optional[DocumentStatus] = None) -> List[Dict]:
        """列出所有文档，可按状态筛选"""
        query = "SELECT * FROM documents"
        params: List = []
        if status:
            query += " WHERE status = ?"
            params.append(status.value)
        query += " ORDER BY created_at DESC"

        async with self.connect() as conn:
            async with conn.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_status(self, task_id: str) -> Optional[DocumentStatus]:
        """获取任务状态"""
        doc = await self.get_document(task_id)
        return DocumentStatus(doc['status']) if doc else None


# 单例实例
_db_instance: Optional[Database] = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        cfg = Config.get_section('storage')
        _db_instance = Database(cfg['db_path'])
    return _db_instance
