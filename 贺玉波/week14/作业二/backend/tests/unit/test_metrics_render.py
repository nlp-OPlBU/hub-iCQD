"""进程内指标的渲染口径：保证 /metrics 输出能被 Prometheus 正确解析。"""

from __future__ import annotations

import pytest

from app.core import metrics


@pytest.fixture(autouse=True)
def clean() -> None:
    metrics.reset()
    yield
    metrics.reset()


def test_counter_renders_type_and_sorted_labels() -> None:
    metrics.inc("rag_http_requests_total", method="GET", route="/api/v1/search")
    metrics.inc("rag_http_requests_total", route="/api/v1/search", method="GET")
    lines = metrics.render().splitlines()
    assert lines[0] == "# TYPE rag_http_requests_total counter"
    # 标签按键名排序，同名同标签的两次自增合并为一行
    assert lines[1] == 'rag_http_requests_total{method="GET",route="/api/v1/search"} 2'


def test_gauge_reports_current_value() -> None:
    metrics.set_gauge("rag_kafka_consumer_lag", 7, topic="doc.ingest")
    metrics.set_gauge("rag_kafka_consumer_lag", 3, topic="doc.ingest")
    assert 'rag_kafka_consumer_lag{topic="doc.ingest"} 3' in metrics.render()


def test_histogram_buckets_sum_and_count() -> None:
    for value in (3, 12, 800):
        metrics.observe("rag_span_duration_ms", value, span="search.knn")
    text = metrics.render()
    # 累积桶：3ms 落 ≤5，12ms 落 ≤25，800ms 落 ≤1000
    assert 'rag_span_duration_ms_bucket{span="search.knn",le="5"} 1' in text
    assert 'rag_span_duration_ms_bucket{span="search.knn",le="25"} 2' in text
    assert 'rag_span_duration_ms_bucket{span="search.knn",le="1000"} 3' in text
    assert 'rag_span_duration_ms_bucket{span="search.knn",le="+Inf"} 3' in text
    assert 'rag_span_duration_ms_sum{span="search.knn"} 815.000' in text
    assert 'rag_span_duration_ms_count{span="search.knn"} 3' in text


def test_type_declared_once_per_name() -> None:
    metrics.inc("rag_x_total", a="1")
    metrics.inc("rag_x_total", a="2")
    text = metrics.render()
    assert text.count("# TYPE rag_x_total counter") == 1


def test_label_escaping() -> None:
    metrics.inc("rag_http_requests_total", route='/a"b\\c')
    assert 'route="/a\\"b\\\\c"' in metrics.render()


def test_empty_render_is_empty_string() -> None:
    assert metrics.render() == ""


def test_snapshot_merges_counters_and_histograms() -> None:
    metrics.inc("rag_http_requests_total", method="GET")
    metrics.observe("rag_span_duration_ms", 30, span="ingest.parse")
    # 模拟另一个进程（worker）的快照：同名 counter 相加、同名 span 的桶与样本数累加
    # 桶边界 5/10/25/50/...，30ms 从 ≤50 起累积；series 尾部是 [sum, count]
    series = [0, 0, 0] + [1] * 9 + [40, 1]
    remote = {
        "counters": [
            {"name": "rag_http_requests_total", "labels": {"method": "GET"}, "value": 2}
        ],
        "histograms": [
            {"name": "rag_span_duration_ms", "labels": {"span": "ingest.parse"}, "series": series}
        ],
        "gauges": [
            {"name": "rag_kafka_consumer_lag", "labels": {"topic": "doc.ingest"}, "value": 5}
        ],
    }
    text = metrics.render([remote])
    assert 'rag_http_requests_total{method="GET"} 3' in text
    assert 'rag_span_duration_ms_count{span="ingest.parse"} 2' in text
    assert 'rag_kafka_consumer_lag{topic="doc.ingest"} 5' in text
    # 合并只作用于本次渲染，本进程状态不受影响
    assert 'rag_span_duration_ms_count{span="ingest.parse"} 1' in metrics.render()


def test_snapshot_round_trip_keeps_labels() -> None:
    metrics.set_gauge("rag_kafka_consumer_lag", 7, topic="doc.ingest", partition="0")
    payload = metrics.snapshot()
    metrics.reset()
    text = metrics.render([payload])
    assert 'rag_kafka_consumer_lag{partition="0",topic="doc.ingest"} 7' in text
