"""检索质量指标：全部是纯函数，方便单测与离线复算。

约定：
- ``retrieved`` 是检索返回的 chunk_id 列表，按名次从高到低；
- ``relevant`` 是标注为该问题正确答案的 chunk_id 集合；
- 多模态场景下 chunk_id 可能来自文本通道，也可能来自图片通道，二者同空间比较。
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from typing import Any


def hit_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """top-k 里是否至少命中一个：衡量“能不能找到”。"""
    target = set(relevant)
    return 1.0 if target & set(retrieved[:k]) else 0.0


def recall_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """top-k 命中的相关块占全部相关块的比例：衡量“找全了没有”。"""
    target = set(relevant)
    if not target:
        return 0.0
    return len(target & set(retrieved[:k])) / len(target)


def mrr(retrieved: list[str], relevant: Iterable[str]) -> float:
    """第一个相关结果名次的倒数：衡量“排得靠不靠前”。"""
    target = set(relevant)
    for index, chunk_id in enumerate(retrieved, start=1):
        if chunk_id in target:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: list[str], relevant: Iterable[str], k: int) -> float:
    """二值相关性下的 nDCG@k；比 recall 更惩罚“相关结果排在后面”。"""
    target = set(relevant)
    if not target:
        return 0.0
    dcg = 0.0
    for index, chunk_id in enumerate(retrieved[:k], start=1):
        if chunk_id in target:
            dcg += 1.0 / math.log2(index + 1)
    ideal = sum(1.0 / math.log2(index + 1) for index in range(1, min(len(target), k) + 1))
    return dcg / ideal if ideal else 0.0


def aggregate(rows: list[dict[str, Any]]) -> dict[str, float]:
    """对逐条结果求均值；空列表返回全 0 而不是抛错，方便报告稳定输出。

    只聚合数值字段：评测详情里还带着 query / miss 这类非数值信息，
    混进来会直接抛 TypeError。
    """
    if not rows:
        return {}
    keys = [key for key, value in rows[0].items() if isinstance(value, (int, float))]
    return {key: round(sum(row[key] for row in rows) / len(rows), 4) for key in keys}


def percentile(values: list[float], ratio: float) -> float:
    """最近秩法百分位：比线性插值更保守，适合延迟这种长尾分布。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, math.ceil(ratio * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]
