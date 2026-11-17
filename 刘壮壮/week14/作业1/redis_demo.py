"""Redis 对话历史的命令行演示。"""

import os

from dotenv import load_dotenv

from redis_chat import ChatHistory, create_redis_client

load_dotenv()


def main() -> None:
    client = create_redis_client()
    client.ping()
    history = ChatHistory(client, ttl=int(os.getenv("REDIS_TTL_SECONDS", "86400")))
    conversation_id = input("会话 ID（直接回车使用 demo）：").strip() or "demo"
    print("输入消息开始对话；输入 /history 查看历史，/clear 清空，/quit 退出。")

    while True:
        content = input("你：").strip()
        if content == "/quit":
            break
        if content == "/history":
            for message in history.get_messages(conversation_id):
                print(f"{message['role']}: {message['content']} ({message['created_at']})")
            continue
        if content == "/clear":
            history.clear(conversation_id)
            print("历史已清空。")
            continue
        if not content:
            continue
        history.add_message(conversation_id, "user", content)
        reply = f"收到：{content}"
        history.add_message(conversation_id, "assistant", reply)
        print(f"助手：{reply}")

    client.close()


if __name__ == "__main__":
    main()
