"""作业1-A：Redis 会话缓存与答案缓存演示。

验证三件事：
1. 多轮对话按 session 隔离地存进 Redis（List 结构），可直接回放；
2. TTL 到期自动清理，会话不会无限增长；
3. 相同问题命中答案缓存，把 600ms 的"模型调用"压到 1ms 级。

用法（在 week14 根目录）：
    PYTHONPATH=backend python demo/redis_chat.py --rounds 4
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
import uuid

from app.core.config import get_settings
from app.core.logging import setup_logging
from app.infra.cache.redis_client import close_redis, get_redis

# 演示用：模拟一次 LLM 生成耗时
FAKE_LLM_LATENCY = 0.6
# 答案缓存 TTL：真实项目里由业务决定，这里取 120 秒便于观察
ANSWER_TTL = 120


def fake_llm(question: str) -> str:
    """假装调用大模型：固定耗时，便于对比缓存前后差异。"""
    time.sleep(FAKE_LLM_LATENCY)
    return f"（模拟回答）关于「{question}」的要点有三条：定义、场景、限制。"


def append_turn(r, session_id: str, role: str, content: str) -> int:
    """写一条消息到会话列表，并刷新 TTL。返回当前消息数。"""
    key = f"chat:{session_id}"
    item = json.dumps({"role": role, "content": content, "ts": int(time.time() * 1000)}, ensure_ascii=False)
    pipe = r.pipeline()
    pipe.rpush(key, item)
    pipe.expire(key, get_settings().redis.chat_ttl_seconds)
    pipe.execute()
    return r.llen(key)


def load_history(r, session_id: str) -> list[dict]:
    return [json.loads(row) for row in r.lrange(f"chat:{session_id}", 0, -1)]


def ask_with_cache(r, question: str) -> tuple[str, bool, float]:
    """先查缓存，未命中再"调用模型"。返回 (答案, 是否命中, 耗时毫秒)。"""
    digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:16]
    key = f"qa:{digest}"
    started = time.perf_counter()

    cached = r.get(key)
    if cached is not None:
        return cached, True, (time.perf_counter() - started) * 1000

    answer = fake_llm(question)
    r.set(key, answer, ex=ANSWER_TTL)
    return answer, False, (time.perf_counter() - started) * 1000


def demo_ttl(r) -> None:
    """演示 TTL：2 秒后键自动消失，不需要任何清理任务。"""
    key = "demo:ttl"
    r.set(key, "alive", ex=2)
    print(f"  set {key} ex=2        -> exists={r.exists(key)} (ttl={r.ttl(key)}s)")
    time.sleep(2.2)
    print(f"  等待 2.2s 后          -> exists={r.exists(key)} (自动过期)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Redis 会话缓存演示")
    parser.add_argument("--rounds", type=int, default=4, help="对话轮数，默认 4")
    parser.add_argument("--session", default=None, help="会话 ID，默认随机生成")
    args = parser.parse_args()

    setup_logging()
    r = get_redis()
    session_id = args.session or f"demo-{uuid.uuid4().hex[:8]}"

    print(f"\n=== 1. 多轮对话写入（session={session_id}）===")
    for index in range(args.rounds):
        question = "什么是多模态 RAG？" if index == 0 else f"第 {index + 1} 个问题：请再展开一点"
        answer, hit, cost_ms = ask_with_cache(r, question)
        size = append_turn(r, session_id, "user", question)
        append_turn(r, session_id, "assistant", answer)
        mark = "HIT " if hit else "MISS"
        print(f"  [{mark}] {cost_ms:7.1f}ms  消息数={size:2d}  Q: {question[:24]}")

    print("\n=== 2. 回放会话（等价于每次请求重建上下文）===")
    history = load_history(r, session_id)
    for turn in history:
        print(f"  {turn['role']:9s} | {turn['content'][:40]}")

    print(f"\n  会话键 chat:{session_id}  TTL={r.ttl(f'chat:{session_id}')}s  条数={len(history)}")

    print("\n=== 3. 缓存命中对比（同一问题连问 3 次）===")
    probe = f"如何评估 RAG 的召回质量？#{session_id[:4]}"  # 带随机后缀，保证首次必为 MISS
    timings: list[float] = []
    hits = 0
    for _ in range(3):
        _, hit, cost_ms = ask_with_cache(r, probe)
        timings.append(cost_ms)
        hits += int(hit)
        print(f"  {'HIT ' if hit else 'MISS'} {cost_ms:7.1f}ms")
    speedup = timings[0] / max(timings[-1], 0.001)
    print(f"\n  命中率={hits}/3  首次 {timings[0]:.1f}ms -> 末次 {timings[-1]:.1f}ms  提速 {speedup:.0f}x")

    print("\n=== 4. TTL 自动过期 ===")
    demo_ttl(r)

    close_redis()
    print("\n[redis_chat] 演示完成\n")


if __name__ == "__main__":
    main()
