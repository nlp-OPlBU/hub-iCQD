"""使用 Redis List 保存简单的对话历史。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any

import redis
from dotenv import load_dotenv

load_dotenv()


def create_redis_client() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("REDIS_DB", "0")),
        decode_responses=True,
    )


class ChatHistory:
    def __init__(self, client: redis.Redis, ttl: int = 86400) -> None:
        if ttl < 1:
            raise ValueError("ttl 必须大于 0")
        self.client = client
        self.ttl = ttl

    @staticmethod
    def _key(conversation_id: str) -> str:
        return f"homework1:chat:{conversation_id}"

    def add_message(self, conversation_id: str, role: str, content: str) -> None:
        if role not in {"user", "assistant"}:
            raise ValueError("role 只能是 user 或 assistant")
        message: dict[str, Any] = {
            "role": role,
            "content": content,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        key = self._key(conversation_id)
        with self.client.pipeline(transaction=True) as pipe:
            pipe.rpush(key, json.dumps(message, ensure_ascii=False))
            pipe.expire(key, self.ttl)
            pipe.execute()

    def get_messages(self, conversation_id: str, limit: int = 20) -> list[dict[str, Any]]:
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        rows = self.client.lrange(self._key(conversation_id), -limit, -1)
        return [json.loads(row) for row in rows]

    def clear(self, conversation_id: str) -> bool:
        return bool(self.client.delete(self._key(conversation_id)))
