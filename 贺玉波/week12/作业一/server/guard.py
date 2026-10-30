# -*- coding: utf-8 -*-
"""轻量防护组件：请求限流 + 短时结果缓存（进程内实现，无需外部依赖）。"""

import threading
import time
from collections import OrderedDict, deque
from typing import Any, Optional, Tuple


class RateLimiter:
    """滑动窗口限流：同一 key（这里用客户端 IP）在 window 秒内最多 limit 次。"""

    def __init__(self, limit: int, window_seconds: int = 60):
        self.limit = max(1, int(limit))
        self.window = max(1, int(window_seconds))
        self._hits: "OrderedDict[str, deque]" = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, key: str) -> Tuple[bool, int]:
        """返回 (是否放行, 建议重试秒数)。"""
        now = time.monotonic()
        with self._lock:
            hits = self._hits.setdefault(key, deque())
            while hits and now - hits[0] > self.window:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = int(self.window - (now - hits[0])) + 1
                return False, max(retry_after, 1)

            hits.append(now)
            # 顺手清理长期不活跃的 key，避免内存无限增长
            if len(self._hits) > 2000:
                for stale_key in list(self._hits.keys())[:1000]:
                    if not self._hits[stale_key]:
                        self._hits.pop(stale_key, None)
            return True, 0


class TTLCache:
    """带过期时间的 LRU 缓存，用来避免同一张图片重复调用模型。"""

    def __init__(self, ttl_seconds: int = 600, max_entries: int = 200):
        self.ttl = max(1, int(ttl_seconds))
        self.max_entries = max(1, int(max_entries))
        self._data: "OrderedDict[str, Tuple[float, Any]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        now = time.monotonic()
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if now > expires_at:
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return value

    def set(self, key: str, value: Any) -> None:
        expires_at = time.monotonic() + self.ttl
        with self._lock:
            self._data[key] = (expires_at, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
