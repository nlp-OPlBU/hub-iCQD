import json
import os
import urllib.error
import urllib.request

BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8000/api/chat")


def send_message(text: str) -> dict:
    text = text.encode("utf-8", errors="ignore").decode("utf-8")
    payload = json.dumps({"text": text}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        BACKEND_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    print("=== LangGraph 情感识别与陪伴系统（前端进程）===")
    print(f"后端地址: {BACKEND_URL}")
    print("输入内容后按回车发送，输入 exit 或 quit 退出。\n")

    while True:
        try:
            user_input = input("你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("再见！")
            break

        try:
            data = send_message(user_input)
            sentiment_label = "正向 (positive)" if data["sentiment"] == "positive" else "负面 (negative)"
            print(f"[情感识别] {sentiment_label} - {data['sentiment_reason']}")
            print(f"[系统回复] {data['reply']}\n")
        except urllib.error.HTTPError as e:
            print(f"[请求失败] HTTP {e.code}: {e.read().decode('utf-8', errors='ignore')}\n")
        except urllib.error.URLError as e:
            print(f"[连接失败] 请确认后端进程已启动 ({BACKEND_URL})。原因: {e.reason}\n")
        except Exception as e:
            print(f"[异常] {e}\n")


if __name__ == "__main__":
    main()
