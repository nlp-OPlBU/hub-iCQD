"""Redis 实践：用 Redis 存储多轮对话历史。

数据结构：
  chat:history:{session}  List  —— 按时间顺序保存每条消息(JSON)
  chat:session:{session}  Hash  —— 会话元信息(创建时间、最后活跃时间、消息数)
两个 key 都设置 7 天过期，历史最多保留最近 50 条。

用法：
  python3 redis_chat_history.py demo                  # 脚本化演示读写过程
  python3 redis_chat_history.py chat --session s1     # 交互式对话
  python3 redis_chat_history.py show --session s1     # 查看某会话历史
  python3 redis_chat_history.py sessions              # 列出所有会话
"""

import argparse
import json
import os
import time
from datetime import datetime

import redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
MAX_MESSAGES = 50          # 每个会话最多保留的消息条数
TTL_SECONDS = 7 * 24 * 3600


def history_key(session: str) -> str:
    return f"chat:history:{session}"


def session_key(session: str) -> str:
    return f"chat:session:{session}"


class ChatHistoryStore:
    """基于 Redis List + Hash 的会话历史存储。"""

    def __init__(self, url: str = REDIS_URL):
        self.r = redis.Redis.from_url(url, decode_responses=True)
        self.r.ping()  # 连接不上会直接抛异常

    def append(self, session: str, role: str, content: str) -> int:
        """追加一条消息，返回当前会话消息总数。"""
        now = time.time()
        message = json.dumps(
            {"role": role, "content": content, "ts": now}, ensure_ascii=False
        )
        hkey, skey = history_key(session), session_key(session)

        pipe = self.r.pipeline(transaction=True)
        pipe.rpush(hkey, message)                 # 尾部追加
        pipe.ltrim(hkey, -MAX_MESSAGES, -1)       # 只保留最近 N 条
        pipe.expire(hkey, TTL_SECONDS)
        pipe.hsetnx(skey, "created_at", now)
        pipe.hset(skey, mapping={"last_active": now})
        pipe.hincrby(skey, "turns", 1)
        pipe.expire(skey, TTL_SECONDS)
        results = pipe.execute()
        return results[-2]  # hincrby 的结果即累计消息数

    def history(self, session: str) -> list[dict]:
        raw = self.r.lrange(history_key(session), 0, -1)
        return [json.loads(item) for item in raw]

    def clear(self, session: str) -> None:
        self.r.delete(history_key(session), session_key(session))

    def session_info(self, session: str) -> dict:
        return self.r.hgetall(session_key(session))

    def sessions(self) -> list[str]:
        prefix = "chat:session:"
        return [key[len(prefix):] for key in self.r.scan_iter(prefix + "*")]


def fake_reply(history: list[dict]) -> str:
    """占位回复：真实场景可替换为调用大模型，把 history 作为上下文传入。"""
    last = history[-1]["content"]
    user_turns = sum(1 for m in history if m["role"] == "user")
    return f"（示例回复）收到你的第 {user_turns} 条消息：{last}，当前上下文共 {len(history)} 条记录"


def print_history(session: str, messages: list[dict]) -> None:
    print(f"\n=== 会话 {session} 的历史（{len(messages)} 条）===")
    if not messages:
        print("（暂无记录）")
    for m in messages:
        clock = datetime.fromtimestamp(m["ts"]).strftime("%H:%M:%S")
        who = "用户" if m["role"] == "user" else "助手"
        print(f"[{clock}] {who}: {m['content']}")
    print()


def cmd_demo(store: ChatHistoryStore) -> None:
    session = "demo-session"
    store.clear(session)
    print(f"已清空会话 {session}，开始写入对话……")

    turns = [
        "你好，帮我记个笔记",
        "主题是 Redis 的基本用法",
    ]
    for text in turns:
        count = store.append(session, "user", text)
        reply = fake_reply(store.history(session))
        count = store.append(session, "assistant", reply)
        print(f"  写入第 {count} 条消息后，Redis List 长度 = {store.r.llen(history_key(session))}")

    print_history(session, store.history(session))
    print("会话元信息(Hash):", store.session_info(session))
    print("剩余过期时间(秒):", store.r.ttl(history_key(session)))


def cmd_chat(store: ChatHistoryStore, session: str) -> None:
    print(f"进入会话 {session}（命令：/history /clear /quit）")
    print_history(session, store.history(session))
    while True:
        try:
            text = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text == "/quit":
            break
        if text == "/history":
            print_history(session, store.history(session))
            continue
        if text == "/clear":
            store.clear(session)
            print("已清空该会话。")
            continue

        store.append(session, "user", text)
        history = store.history(session)
        reply = fake_reply(history)
        store.append(session, "assistant", reply)
        print(f"助手: {reply}   (Redis 已存 {len(store.history(session))} 条)")


def main() -> None:
    parser = argparse.ArgumentParser(description="用 Redis 存储对话历史")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="运行脚本化演示")
    chat = sub.add_parser("chat", help="交互式对话")
    chat.add_argument("--session", default="default")
    show = sub.add_parser("show", help="查看会话历史")
    show.add_argument("--session", default="default")
    sub.add_parser("sessions", help="列出所有会话")

    args = parser.parse_args()
    store = ChatHistoryStore()

    if args.command == "demo":
        cmd_demo(store)
    elif args.command == "chat":
        cmd_chat(store, args.session)
    elif args.command == "show":
        print_history(args.session, store.history(args.session))
    elif args.command == "sessions":
        print("会话列表:", store.sessions())


if __name__ == "__main__":
    main()
