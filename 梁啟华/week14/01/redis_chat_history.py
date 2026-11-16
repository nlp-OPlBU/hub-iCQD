#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
程序一：用 Redis 保存多轮对话历史

功能：
  1. 按会话 id 追加消息（role + content + timestamp）；
  2. 取出完整历史，并还原成按时间先后排列的对话列表；
  3. 每个会话只保留最近 N 条（超过后自动裁掉最旧的）；
  4. 给会话设置过期时间（每次追加都会续期），过期后 Redis 自动清理。

为什么这样选数据结构（面试/工程里都常问）：
  * List  chat:session:<id>:messages
      - RPUSH 是原子追加，天然保持写入顺序，LRANGE 0 -1 读出来就是完整时间线；
      - 如果用一个 String 存整个 JSON 数组，每次追加都要“读-改-写”，并发时会互相覆盖；
      - LTRIM -N -1 可以 O(1) 地只保留最近 N 条，正好对应“滑动窗口”需求。
  * Hash  chat:session:<id>:meta
      - 会话标题、创建/更新时间、消息条数是独立字段，可以单独 HSET 更新，
        不需要把整个会话再序列化一遍；
      - 用 HGETALL 一次拿全，做会话列表/详情页很自然。
  * 不选 ZSet：历史回顾通常就是“按写入顺序”，List 已经满足；
    ZSet 的 score 若用毫秒时间戳，同一毫秒内的消息还会冲突。

运行：
    python redis_chat_history.py                 # 进入交互模式
    python redis_chat_history.py --demo          # 自动写入一段示例并展示效果
    python redis_chat_history.py --session s1    # 指定会话 id
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

import redis

from config_loader import load_config


def _now_iso() -> str:
    """带时区的 ISO 时间字符串，便于跨时区排查；排序时用字符串比较也成立。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _title_from(content: str) -> str:
    """用第一条消息生成会话标题，过长则截断。"""
    text = " ".join(content.split())
    return text[:20] + ("…" if len(text) > 20 else "")


def _format_ttl(seconds: int) -> str:
    """把 Redis 返回的 TTL（秒）翻译成人话。"""
    if seconds == -1:
        return "未设置过期（永不过期）"
    if seconds == -2:
        return "key 不存在"
    return f"{seconds} 秒（约 {seconds / 3600:.1f} 小时）"


class RedisChatHistory:
    """把“会话历史”这层业务含义封装在 Redis 的 List + Hash 之上。"""

    def __init__(self, redis_config: dict):
        self.max_messages = int(redis_config.get("max_messages", 50))
        self.ttl_seconds = int(redis_config.get("ttl_seconds", 0))
        self.key_prefix = (redis_config.get("key_prefix") or "chat").strip(":") or "chat"

        # 连接参数全部来自 config.yaml，密码为空时不要传空字符串给 redis-py
        self.client = redis.Redis(
            host=redis_config["host"],
            port=int(redis_config["port"]),
            db=int(redis_config.get("db", 0)),
            password=redis_config.get("password") or None,
            socket_timeout=float(redis_config.get("socket_timeout_seconds", 3)),
            # decode_responses=True：让 redis-py 直接把 bytes 解成 str，省去手工 decode
            decode_responses=True,
        )
        # 启动就 ping 一次：配置错了能立刻看到友好错误，而不是等第一次读写才报错
        self.client.ping()

    # ------------------------------------------------------------------ key
    def _messages_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:session:{session_id}:messages"

    def _meta_key(self, session_id: str) -> str:
        return f"{self.key_prefix}:session:{session_id}:meta"

    # --------------------------------------------------------------- 写入
    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        keep_last: int | None = None,
    ) -> int:
        """
        追加一条消息，返回追加后的消息总数。

        一次追加会做四件事：
          1. RPUSH 到 List 尾部（最新消息在尾部）；
          2. LTRIM 只保留最近 keep_last 条；
          3. 更新 Hash 元信息；
          4. 重新设置过期时间（滑动过期：越活跃的会话保留越久）。
        """
        keep_last = self.max_messages if keep_last is None else int(keep_last)
        now = _now_iso()
        message = {"role": role, "content": content, "timestamp": now}
        messages_key = self._messages_key(session_id)
        meta_key = self._meta_key(session_id)

        # pipeline 把多条命令打包成一次网络往返，减少 RTT；
        # transaction=True 表示 MULTI/EXEC，保证“追加 + 截断”要么都成功要么都失败
        pipe = self.client.pipeline(transaction=True)
        pipe.rpush(messages_key, json.dumps(message, ensure_ascii=False))
        if keep_last > 0:
            # LTRIM -N -1：只保留下标 -N 到 -1，也就是最新的 N 条
            pipe.ltrim(messages_key, -keep_last, -1)
        pipe.execute()

        count = self.client.llen(messages_key)

        # HSETNX 只在字段不存在时写入，保证 title / created_at 是“第一条消息”的信息
        self.client.hsetnx(meta_key, "created_at", now)
        self.client.hsetnx(meta_key, "title", _title_from(content))
        self.client.hset(
            meta_key,
            mapping={
                "session_id": session_id,
                "updated_at": now,
                "message_count": count,
                "last_role": role,
                "ttl_seconds": self.ttl_seconds,
            },
        )

        self._refresh_ttl(session_id)
        return count

    def _refresh_ttl(self, session_id: str) -> None:
        """给消息 List 和元信息 Hash 同时续期。ttl_seconds <= 0 表示不设置过期。"""
        if self.ttl_seconds <= 0:
            return
        pipe = self.client.pipeline(transaction=False)
        pipe.expire(self._messages_key(session_id), self.ttl_seconds)
        pipe.expire(self._meta_key(session_id), self.ttl_seconds)
        pipe.execute()

    # --------------------------------------------------------------- 读取
    def get_history(self, session_id: str) -> list[dict]:
        """
        取出完整历史。

        RPUSH 决定了“旧消息在前、新消息在后”，因此 LRANGE 0 -1 拿到的就是
        天然有序的列表，逐条 json.loads 即可还原成最初的对话。
        """
        raw_items = self.client.lrange(self._messages_key(session_id), 0, -1)
        history: list[dict] = []
        for raw in raw_items:
            try:
                history.append(json.loads(raw))
            except json.JSONDecodeError:
                # 教学示例里不太会出现；真实系统遇到脏数据时跳过并记录日志更合适
                continue
        return history

    def get_meta(self, session_id: str) -> dict:
        meta = self.client.hgetall(self._meta_key(session_id))
        if "message_count" in meta:
            meta["message_count"] = int(meta["message_count"])
        return meta

    def ttl(self, session_id: str) -> int:
        """返回会话剩余的过期秒数（-1 永不过期，-2 不存在）。"""
        return self.client.ttl(self._messages_key(session_id))

    # --------------------------------------------------------------- 维护
    def trim(self, session_id: str, keep_last: int) -> int:
        """手动只保留最近 keep_last 条，返回剩余条数。"""
        if keep_last <= 0:
            raise ValueError("keep_last 必须大于 0")
        messages_key = self._messages_key(session_id)
        self.client.ltrim(messages_key, -keep_last, -1)
        count = self.client.llen(messages_key)
        self.client.hset(self._meta_key(session_id), "message_count", count)
        return count

    def set_ttl(self, session_id: str, seconds: int) -> int:
        """单独设置/修改过期时间（秒），返回设置后的 TTL。"""
        if seconds <= 0:
            # EXPIRE 传非正数会立即删除 key，这里显式拒绝，避免误删教学数据
            raise ValueError("过期时间必须大于 0 秒")
        pipe = self.client.pipeline(transaction=False)
        pipe.expire(self._messages_key(session_id), seconds)
        pipe.expire(self._meta_key(session_id), seconds)
        pipe.hset(self._meta_key(session_id), "ttl_seconds", seconds)
        pipe.execute()
        self.ttl_seconds = seconds  # 后续追加消息沿用新的过期时间
        return seconds

    def clear(self, session_id: str) -> int:
        """删除整个会话（消息 + 元信息），返回删除的 key 数量。"""
        return self.client.delete(
            self._messages_key(session_id), self._meta_key(session_id)
        )

    def close(self) -> None:
        self.client.close()


# ------------------------------------------------------------------ 展示
def print_history(messages: list[dict]) -> None:
    if not messages:
        print("  （暂无历史）")
        return
    for index, message in enumerate(messages, start=1):
        role = message.get("role", "unknown")
        timestamp = message.get("timestamp", "-")
        content = message.get("content", "")
        print(f"  {index:>2}. [{timestamp}] {role:<9} | {content}")


def print_meta(meta: dict, ttl_seconds: int) -> None:
    if not meta:
        print("  （暂无会话元信息）")
        return
    print(f"  会话 id     : {meta.get('session_id', '-')}")
    print(f"  标题        : {meta.get('title', '-')}")
    print(f"  创建时间    : {meta.get('created_at', '-')}")
    print(f"  最后更新    : {meta.get('updated_at', '-')}")
    print(f"  消息条数    : {meta.get('message_count', 0)}")
    print(f"  最后角色    : {meta.get('last_role', '-')}")
    print(f"  剩余过期时间: {_format_ttl(ttl_seconds)}")


def print_help() -> None:
    print(
        """
可用命令：
  /history          查看当前会话完整历史（按写入顺序还原）
  /meta             查看会话元信息（标题、条数、更新时间、剩余 TTL）
  /trim N           只保留最近 N 条消息
  /ttl              查看剩余过期时间
  /ttl 秒数         重设过期时间（例如 /ttl 600）
  /switch 会话id    切换到另一个会话
  /role 角色        切换当前角色：user / assistant / system
  /clear            删除当前会话
  /demo             在当前会话自动写入一段示例对话
  /help             显示本帮助
  /quit             退出
直接输入文本 = 以当前角色追加一条消息。
        """.strip()
    )


def run_demo(store: RedisChatHistory, session_id: str) -> None:
    """非交互演示：写入一段对话、查看历史与元信息，再演示“只保留最近 N 条”。"""
    print(f"\n[演示] 向会话 {session_id!r} 写入 4 条消息……")
    sample_dialog = [
        ("user", "帮我用 Python 写一个快速排序"),
        ("assistant", "可以，核心是选一个基准值，把比它小的放左边、大的放右边，再递归处理。"),
        ("user", "能给每行加上中文注释吗？"),
        ("assistant", "当然可以，下面是带注释的版本：def quick_sort(items): ..."),
    ]
    for role, content in sample_dialog:
        count = store.append_message(session_id, role, content)
        print(f"  + {role:<9} | {content[:36]}…  (累计 {count} 条)")

    print("\n[演示] 读回完整历史（List 从旧到新，直接还原对话顺序）：")
    print_history(store.get_history(session_id))
    print("\n[演示] 会话元信息（Hash）：")
    print_meta(store.get_meta(session_id), store.ttl(session_id))

    # 用另一个会话演示裁剪，避免破坏上面展示的完整历史
    trim_session = f"{session_id}-trim"
    print(f"\n[演示] 在新会话 {trim_session!r} 写入 6 条，然后只保留最近 3 条：")
    for index in range(1, 7):
        store.append_message(trim_session, "user", f"第 {index} 条消息")
    store.trim(trim_session, 3)
    print_history(store.get_history(trim_session))

    print("\n[提示] 上面的数据会按配置的 TTL 自动过期；再次运行可看到 TTL 被续期。")


def run_interactive(store: RedisChatHistory, session_id: str, role: str = "user") -> None:
    """交互式命令行循环：输入文本即可看到历史累加。"""
    print(f"已连接 Redis，当前会话：{session_id}，当前角色：{role}")
    print_help()

    while True:
        try:
            line = input(f"\n[{session_id}] {role}> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return

        if not line:
            continue

        # ---------- 斜杠命令 ----------
        if line.startswith("/"):
            command, _, argument = line[1:].partition(" ")
            command = command.lower()
            argument = argument.strip()

            if command in {"quit", "exit"}:
                print("已退出。")
                return
            if command == "help":
                print_help()
            elif command == "history":
                print(f"会话 {session_id!r} 的完整历史：")
                print_history(store.get_history(session_id))
            elif command == "meta":
                print_meta(store.get_meta(session_id), store.ttl(session_id))
            elif command == "trim":
                try:
                    remain = store.trim(session_id, int(argument))
                    print(f"已裁剪，当前保留最近 {remain} 条。")
                except ValueError as exc:
                    print(f"用法错误：/trim N（N 为正整数）。{exc}")
            elif command == "ttl":
                if not argument:
                    print(f"剩余过期时间：{_format_ttl(store.ttl(session_id))}")
                else:
                    try:
                        seconds = store.set_ttl(session_id, int(argument))
                        print(f"已设置过期时间：{seconds} 秒")
                    except ValueError as exc:
                        print(f"用法错误：/ttl 秒数（秒数为正整数）。{exc}")
            elif command == "switch":
                if not argument:
                    print("用法：/switch 会话id")
                else:
                    session_id = argument
                    print(f"已切换到会话：{session_id}")
            elif command == "role":
                if argument not in {"user", "assistant", "system"}:
                    print("角色只能是 user / assistant / system")
                else:
                    role = argument
                    print(f"当前角色已切换为：{role}")
            elif command == "clear":
                deleted = store.clear(session_id)
                print(f"已删除会话 {session_id!r}（删除 {deleted} 个 key）。")
            elif command == "demo":
                run_demo(store, session_id)
            else:
                print(f"未知命令：/{command}，输入 /help 查看帮助。")
            continue

        # ---------- 普通文本：追加一条消息 ----------
        count = store.append_message(session_id, role, line)
        print(
            f"  已追加，当前会话共 {count} 条消息；"
            f"TTL 剩余 {_format_ttl(store.ttl(session_id))}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="用 Redis 保存多轮对话历史（教学示例）")
    parser.add_argument("--config", help="配置文件路径，默认使用脚本同目录的 config.yaml")
    parser.add_argument("--session", default="demo", help="会话 id，默认 demo")
    parser.add_argument("--demo", action="store_true", help="自动演示模式，不进入交互")
    args = parser.parse_args()

    config = load_config(args.config)
    redis_config = config["redis"]

    try:
        store = RedisChatHistory(redis_config)
    except redis.exceptions.AuthenticationError:
        print("[错误] Redis 认证失败：请检查 config.yaml 中的 redis.password。")
        sys.exit(1)
    except redis.exceptions.ConnectionError as exc:
        print("[错误] 无法连接 Redis，请先确认服务已启动。")
        print(f"       当前配置：{redis_config['host']}:{redis_config['port']} "
              f"db={redis_config.get('db', 0)}")
        print("       排查步骤：")
        print("         1) 在本目录执行：docker compose up -d redis")
        print("         2) 执行 docker compose ps 确认 week14-redis 状态为 running")
        print("         3) 检查 config.yaml 的 redis.host / redis.port 是否与端口映射一致")
        print(f"       原始错误：{exc}")
        sys.exit(1)

    try:
        if args.demo:
            run_demo(store, args.session)
        else:
            run_interactive(store, args.session)
    finally:
        store.close()


if __name__ == "__main__":
    main()
