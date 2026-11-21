"""前端进程（CLI）：只通过 HTTP 调用后端，不导入 app 内部模块。"""

from __future__ import annotations

import sys

import httpx

from config import settings

LABELS = {"positive": "正向", "negative": "负面", "neutral": "中性"}


def analyze(text: str) -> dict:
    response = httpx.post(
        f"{settings.backend_url}/api/analyze", json={"text": text}, timeout=90.0
    )
    response.raise_for_status()
    return response.json()


def main() -> int:
    print("情感分析 CLI（LangGraph 版）")
    print(f"后端：{settings.backend_url}    输入空行或 Ctrl+C 退出\n")

    while True:
        try:
            text = input("你说：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            return 0
        if not text:
            print("已退出。")
            return 0

        try:
            result = analyze(text)
        except httpx.HTTPStatusError as exc:
            print(f"[错误] 后端返回 {exc.response.status_code}：{exc.response.text}\n")
            continue
        except httpx.HTTPError as exc:
            print(f"[错误] 连接后端失败：{exc}。请先启动后端进程。\n")
            continue

        print(f"  情感：{LABELS.get(result['sentiment'], result['sentiment'])}"
              f"（置信度 {result['confidence']:.2f}）")
        print(f"  理由：{result['reasoning']}")
        print(f"  回应：{result['response_text']}")
        print(f"  耗时：{result['elapsed_ms']} ms\n")


if __name__ == "__main__":
    sys.exit(main())
