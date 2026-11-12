"""Prometheus 抓取口集成测试：真实 MySQL + Redis 下的计数与延迟。"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core import metrics

pytestmark = pytest.mark.integration


@pytest.fixture()
async def client(redis_clean, mysql_clean):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def test_metrics_exposes_http_request_series(client: AsyncClient) -> None:
    metrics.reset()

    response = await client.get("/api/v1/documents")
    assert response.status_code == 200, response.text

    body = (await client.get("/metrics")).text
    assert "# TYPE rag_http_requests_total counter" in body
    assert 'rag_http_requests_total{method="GET",path="/api/v1/documents",status="200"} 1' in body
    assert 'rag_http_duration_ms_count{method="GET",path="/api/v1/documents",status="200"} 1' in body


async def test_metrics_itself_is_not_counted(client: AsyncClient) -> None:
    metrics.reset()
    await client.get("/metrics")
    body = (await client.get("/metrics")).text
    assert "/metrics" not in body


async def test_metrics_content_type(client: AsyncClient) -> None:
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
