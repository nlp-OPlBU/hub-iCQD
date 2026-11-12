"""进程内指标，直接输出 Prometheus 文本格式。

为什么不引 prometheus_client：指标种类固定，自己维护 counter/histogram
比多一层依赖更可控，也不需要额外 exporter。

注意：指标只在进程内，重启即清零。api 与 ingest-worker 是两个进程，
worker 会周期性用 :func:`snapshot` 把指标写进 Redis，api 在渲染时通过
``render(extra)`` 合并，因此 /metrics 能看到全链路；两边重启仍会清零。
需要跨实例持久聚合时再换 prometheus_client 或推送到远端。
"""

from __future__ import annotations

import threading
from typing import Any, Iterable

_LOCK = threading.Lock()

_COUNTERS: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}

# 单位毫秒，覆盖从内存缓存的 1ms 到冷启动 / 大模型生成的 30s
_BUCKETS: tuple[float, ...] = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000)
_HISTOGRAMS: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]] = {}
_GAUGES: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}


def _key(name: str, labels: dict[str, Any]) -> tuple[str, tuple[tuple[str, str], ...]]:
    return name, tuple(sorted((str(k), str(v)) for k, v in labels.items()))


def inc(name: str, value: float = 1.0, **labels: Any) -> None:
    key = _key(name, labels)
    with _LOCK:
        _COUNTERS[key] = _COUNTERS.get(key, 0.0) + value


def observe(name: str, value: float, **labels: Any) -> None:
    key = _key(name, labels)
    with _LOCK:
        # [各桶计数..., 总和, 样本数]，避免为每个样本存一条记录
        series = _HISTOGRAMS.get(key)
        if series is None:
            series = [0.0] * (len(_BUCKETS) + 2)
            _HISTOGRAMS[key] = series
        for index, bound in enumerate(_BUCKETS):
            if value <= bound:
                series[index] += 1
        series[-2] += value
        series[-1] += 1


def set_gauge(name: str, value: float, **labels: Any) -> None:
    """当前值（如 Kafka 消费 lag），由后台线程周期性覆盖。"""
    key = _key(name, labels)
    with _LOCK:
        _GAUGES[key] = value


def render(extra: Iterable[dict[str, Any]] | None = None) -> str:
    """生成 Prometheus exposition 文本（# TYPE 按名字去重）。

    ``extra`` 是其他进程上报的快照（见 :func:`snapshot`）。合并只在本次渲染的
    副本上做，不会改本进程状态，因此可以安全地重复渲染。
    counter / histogram 按 Prometheus 语义相加，gauge 以后到的快照为准。
    """
    lines: list[str] = []
    declared: set[str] = set()
    with _LOCK:
        counters = dict(_COUNTERS)
        histograms = {key: list(value) for key, value in _HISTOGRAMS.items()}
        gauges = dict(_GAUGES)

    for item in extra or ():
        _merge_snapshot(item, counters, histograms, gauges)

    for (name, labels), value in sorted(gauges.items()):
        if name not in declared:
            lines.append(f"# TYPE {name} gauge")
            declared.add(name)
        lines.append(f"{name}{_format_labels(labels)} {value:.0f}")

    for (name, labels), value in sorted(counters.items()):
        if name not in declared:
            lines.append(f"# TYPE {name} counter")
            declared.add(name)
        lines.append(f"{name}{_format_labels(labels)} {value:.0f}")

    for (name, labels), series in sorted(histograms.items()):
        if name not in declared:
            lines.append(f"# TYPE {name} histogram")
            declared.add(name)
        cumulative = 0.0
        for index, bound in enumerate(_BUCKETS):
            cumulative = series[index]
            lines.append(
                f"{name}_bucket{_format_labels((*labels, ('le', _fmt(bound))))} {cumulative:.0f}"
            )
        lines.append(
            f"{name}_bucket{_format_labels((*labels, ('le', '+Inf')))} {series[-1]:.0f}"
        )
        lines.append(f"{name}_sum{_format_labels(labels)} {series[-2]:.3f}")
        lines.append(f"{name}_count{_format_labels(labels)} {series[-1]:.0f}")
    return "\n".join(lines) + ("\n" if lines else "")


def snapshot() -> dict[str, Any]:
    """导出本进程指标，供其他进程合并展示（JSON 可序列化）。"""
    with _LOCK:
        return {
            "counters": [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in _COUNTERS.items()
            ],
            "histograms": [
                {"name": name, "labels": dict(labels), "series": list(series)}
                for (name, labels), series in _HISTOGRAMS.items()
            ],
            "gauges": [
                {"name": name, "labels": dict(labels), "value": value}
                for (name, labels), value in _GAUGES.items()
            ],
        }


def _merge_snapshot(
    item: dict[str, Any],
    counters: dict[tuple[str, tuple[tuple[str, str], ...]], float],
    histograms: dict[tuple[str, tuple[tuple[str, str], ...]], list[float]],
    gauges: dict[tuple[str, tuple[tuple[str, str], ...]], float],
) -> None:
    for row in item.get("counters") or ():
        key = _key(str(row.get("name")), row.get("labels") or {})
        counters[key] = counters.get(key, 0.0) + float(row.get("value") or 0.0)
    for row in item.get("histograms") or ():
        incoming = [float(value) for value in (row.get("series") or ())]
        if not incoming:
            continue
        key = _key(str(row.get("name")), row.get("labels") or {})
        current = histograms.get(key)
        if current is None:
            histograms[key] = incoming
            continue
        # 两边的桶边界版本可能不同，按较短长度相加，避免越界
        for index in range(min(len(current), len(incoming))):
            current[index] += incoming[index]
    for row in item.get("gauges") or ():
        key = _key(str(row.get("name")), row.get("labels") or {})
        gauges[key] = float(row.get("value") or 0.0)


def reset() -> None:
    """测试用：清空所有指标。"""
    with _LOCK:
        _COUNTERS.clear()
        _HISTOGRAMS.clear()
        _GAUGES.clear()


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _format_labels(labels: tuple[tuple[str, str], ...]) -> str:
    if not labels:
        return ""
    body = ",".join(f'{key}="{_escape(value)}"' for key, value in labels)
    return "{" + body + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
