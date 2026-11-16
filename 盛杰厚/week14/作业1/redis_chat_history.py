#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 Redis 保存「聊天历史」—— Redis 数据结构实战

对话历史天然是「按会话分组、按时间追加、只关心最近若干条」的数据，
正好对应 Redis 的三种结构：

    chat:{session_id}:messages   List  每条消息一条 JSON，RPUSH 追加、LRANGE 读最近 N 条、LTRIM 裁剪
    chat:{session_id}:meta       Hash  会话元信息：创建时间、更新时间、轮数、最后一句
    chat:{session_id}:seq        String 自增序号，给每条消息一个稳定 seq
    chat:sessions                ZSet  会话索引，score 用「最后更新时间」，按活跃度倒序翻页

另外每次写入都刷新 EXPIRE，会话闲置一段时间自动过期清除（这里默认 7 天）。

运行：
    python redis_chat_history.py                       # 跑内置演示
    python redis_chat_history.py --keep                # 演示前不清空已有的 chat:* 数据
    python redis_chat_history.py --url redis://localhost:6379/0

演示结束后数据会保留在 Redis 里，方便用 redis-cli 自己翻：
    redis-cli LRANGE chat:demo-1001:messages 0 -1
    redis-cli HGETALL chat:demo-1001:meta
    redis-cli ZREVRANGE chat:sessions 0 -1 WITHSCORES
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import redis

DEFAULT_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


def now_human(ts: Optional[float] = None) -> str:
    ts = time.time() if ts is None else ts
    return datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


class ChatHistory:
    """把对话历史存进 Redis 的小工具类。"""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        prefix: str = "chat",
        ttl_seconds: int = 7 * 24 * 3600,   # 会话闲置多久过期
        max_messages: int = 20,             # 每个会话最多保留多少条消息
    ) -> None:
        self.r = redis.Redis.from_url(url, decode_responses=True)
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.max_messages = max_messages
        self.sessions_key = f"{prefix}:sessions"

    # ---------- key 约定：所有 key 都从一个地方生成，避免拼错 ----------
    def _key(self, session_id: str, suffix: str) -> str:
        return f"{self.prefix}:{session_id}:{suffix}"

    def _msgs(self, sid: str) -> str:
        return self._key(sid, "messages")

    def _meta(self, sid: str) -> str:
        return self._key(sid, "meta")

    def _seq(self, sid: str) -> str:
        return self._key(sid, "seq")

    # ---------- 写 ----------
    def append(self, session_id: str, role: str, content: str, **extra: Any) -> Dict[str, Any]:
        """追加一条消息，并同步维护元信息、会话索引、过期时间。

        用 pipeline 把这一批命令打包一次发出去：要么都成功，要么都不成功，
        避免出现「消息写进去了、索引没更新」这种半截状态。
        """
        ts = time.time()
        seq = self.r.incr(self._seq(session_id))        # 需要返回值，所以单独先执行
        message = {
            "seq": seq,
            "role": role,                                # user / assistant / system
            "content": content,
            "ts": ts,
            "time": now_human(ts),
        }
        message.update(extra)

        pipe = self.r.pipeline(transaction=True)
        pipe.rpush(self._msgs(session_id), json.dumps(message, ensure_ascii=False))
        pipe.ltrim(self._msgs(session_id), -self.max_messages, -1)   # 只留最近 N 条
        pipe.expire(self._msgs(session_id), self.ttl_seconds)

        pipe.hsetnx(self._meta(session_id), "created_at", now_human(ts))
        pipe.hsetnx(self._meta(session_id), "session_id", session_id)
        pipe.hset(self._meta(session_id), mapping={"updated_at": now_human(ts)})
        pipe.hincrby(self._meta(session_id), "turns", 1)
        pipe.hset(self._meta(session_id), mapping={"last_role": role, "last_message": content[:80]})
        pipe.expire(self._meta(session_id), self.ttl_seconds)
        pipe.expire(self._seq(session_id), self.ttl_seconds)

        # 会话索引：score 用时间戳，ZREVRANGE 就是「最近活跃的会话排前面」
        pipe.zadd(self.sessions_key, {session_id: ts})
        pipe.execute()

        return message

    # ---------- 读 ----------
    def history(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """按时间顺序返回最近 limit 条消息（LRANGE 负数下标 = 从尾部数）。"""
        raw = self.r.lrange(self._msgs(session_id), -limit, -1)
        return [json.loads(item) for item in raw]

    def build_llm_context(
        self,
        session_id: str,
        limit: int = 20,
        system_prompt: str = "你是一个乐于助人的助手。",
    ) -> List[Dict[str, str]]:
        """把历史拼成大模型 API 要的 messages 格式 —— 这就是「记忆」的实际用法。"""
        context: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        for msg in self.history(session_id, limit):
            context.append({"role": msg["role"], "content": msg["content"]})
        return context

    def meta(self, session_id: str) -> Dict[str, str]:
        return self.r.hgetall(self._meta(session_id))

    def list_sessions(self, limit: int = 10) -> List[Dict[str, Any]]:
        rows = self.r.zrevrange(self.sessions_key, 0, limit - 1, withscores=True)
        return [
            {
                "session_id": sid,
                "updated_at": now_human(score),
                "alive": self.r.exists(self._msgs(sid)) > 0,
            }
            for sid, score in rows
        ]

    def prune_session_index(self) -> int:
        """会话过期后 List 会没，但 ZSet 里还会留下「幽灵索引」，这里清理掉。"""
        removed = 0
        for sid in self.r.zrange(self.sessions_key, 0, -1):
            if not self.r.exists(self._msgs(sid)):
                self.r.zrem(self.sessions_key, sid)
                removed += 1
        return removed

    def ttl(self, session_id: str) -> int:
        return self.r.ttl(self._msgs(session_id))

    # ---------- 删 ----------
    def delete_session(self, session_id: str) -> None:
        self.r.delete(self._msgs(session_id), self._meta(session_id), self._seq(session_id))
        self.r.zrem(self.sessions_key, session_id)

    def reset_all(self) -> int:
        """删掉本程序前缀下的所有 key（演示用；生产环境慎用 KEYS/SCAN 全删）。"""
        deleted = 0
        for key in self.r.scan_iter(match=f"{self.prefix}:*", count=200):
            deleted += self.r.delete(key)
        return deleted


# ----------------------------- 演示 -----------------------------
DIALOG = [
    ("demo-1001", "user", "我想用 Redis 存聊天记录，应该选什么数据结构？"),
    ("demo-1001", "assistant", "优先用 List：RPUSH 追加、LRANGE 取最近 N 条，天然按时间排序。"),
    ("demo-1001", "user", "那历史越攒越多怎么办？"),
    ("demo-1001", "assistant", "用 LTRIM 只保留最近 N 条，再给会话 key 设 EXPIRE，闲置自动清掉。"),
    ("demo-1001", "user", "多个用户的会话怎么区分？"),
    ("demo-1001", "assistant", "每个会话一个 key，比如 chat:{session_id}:messages；再用 ZSet 按更新时间给会话排序。"),
    ("demo-1002", "user", "你好，还记得我上次问过什么吗？"),
    ("demo-1002", "assistant", "记得，你上次问的是 Redis 该用什么结构存聊天记录。"),
]


def show(title: str) -> None:
    print(f"\n{'=' * 12} {title} {'=' * 12}")


def demo(url: str, keep: bool) -> int:
    store = ChatHistory(url=url, max_messages=20)

    try:
        info = store.r.info("server")
        pong = store.r.ping()
    except redis.exceptions.ConnectionError as exc:
        print(f"[!] 连不上 Redis（{url}）：{exc}")
        print("    启动一个本地 Redis 再试，例如：")
        print("      docker run -d -p 6379:6379 redis:7-alpine")
        print("      # 或已安装 redis-server：redis-server --port 6379 --daemonize yes")
        return 1

    print(f"已连接 Redis {info.get('redis_version')}，PING -> {pong}")

    if not keep:
        removed = store.reset_all()
        print(f"演示前清理旧的 chat:* 数据：删除 {removed} 个 key")

    # 1. 写入一段对话
    show("1. 写入对话历史（RPUSH + LTRIM + EXPIRE，pipeline 一次提交）")
    for sid, role, content in DIALOG:
        msg = store.append(sid, role, content, user_id="u-42")
        print(f"  [{sid}] #{msg['seq']:>2} {role:<9} {content}")

    # 2. 读取某个会话
    show("2. 读取会话 demo-1001 的历史（LRANGE 最近 3 条）")
    for msg in store.history("demo-1001", limit=3):
        print(f"  #{msg['seq']:>2} {msg['time']} {msg['role']:<9} {msg['content']}")

    # 3. 给大模型用的上下文
    show("3. 拼成 LLM messages（这就是「对话记忆」真正被用上的地方）")
    context = store.build_llm_context("demo-1001", limit=20)
    print(json.dumps(context, ensure_ascii=False, indent=2))

    # 4. 元信息与会话索引
    show("4. 会话元信息（Hash）")
    print(json.dumps(store.meta("demo-1001"), ensure_ascii=False, indent=2))

    show("5. 会话列表（ZSet，按最后更新时间倒序）")
    for row in store.list_sessions():
        print(f"  {row['session_id']:<12} 最后活跃 {row['updated_at']}  存活={row['alive']}")

    # 6. 长对话自动裁剪
    show("6. 长对话裁剪：max_messages=6，写 10 条后只剩最近 6 条")
    trim_store = ChatHistory(url=url, max_messages=6)
    for i in range(1, 11):
        trim_store.append("demo-trim", "user" if i % 2 else "assistant", f"第 {i} 条消息")
    kept = trim_store.history("demo-trim", limit=100)
    seqs = [m["seq"] for m in kept]
    print(f"  List 长度 = {trim_store.r.llen(trim_store._msgs('demo-trim'))}（只保留最近 6 条）")
    print(f"  保留下来的 seq = {seqs}  ← 序号连续增长，说明老数据是被裁掉的，不是被覆盖的")

    # 7. 过期时间
    show("7. 会话过期时间（每次写入都会刷新 TTL）")
    ttl = store.ttl("demo-1001")
    print(f"  chat:demo-1001:messages 剩余 TTL = {ttl} 秒（约 {ttl / 3600:.1f} 小时）")

    # 8. 索引清理 + 删除
    show("8. 清理失联索引 / 删除单个会话")
    store.r.delete(store._msgs("demo-1002"))       # 模拟会话 List 已过期，但索引还在
    pruned = store.prune_session_index()
    print(f"  清理掉 {pruned} 个「List 已过期但索引还在」的会话条目")
    store.delete_session("demo-trim")
    print(f"  删除 demo-trim 后，剩余会话：{[x['session_id'] for x in store.list_sessions()]}")

    # 9. 交给你自己检查
    show("9. 现在可以用 redis-cli 自己验证")
    print("  redis-cli LRANGE chat:demo-1001:messages 0 -1")
    print("  redis-cli HGETALL chat:demo-1001:meta")
    print("  redis-cli ZREVRANGE chat:sessions 0 -1 WITHSCORES")
    print("  redis-cli TTL chat:demo-1001:messages")
    print("\n提示：再跑一次演示会先清空 chat:*（加 --keep 可保留）。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用 Redis 保存聊天历史（练习）")
    parser.add_argument("--url", default=DEFAULT_URL, help=f"Redis 连接串，默认 {DEFAULT_URL}")
    parser.add_argument("--keep", action="store_true", help="演示前不清空已有的 chat:* 数据")
    args = parser.parse_args()
    return demo(args.url, args.keep)


if __name__ == "__main__":
    sys.exit(main())
