"""链路查询接口集成测试：真实 MySQL + Redis。

trace 的写入与读取都走真实中间件，验证：
Redis 命中、缓存过期后回落 MySQL、未知 trace 返回 404。
"""

from __future__ import annotations

import time

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.tracing import TraceSession, new_trace_id, span

pytestmark = pytest.mark.integration


@pytest.fixture()
async def client(redis_clean, mysql_schema):
    from app.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _write_trace(trace_id: str) -> None:
    with TraceSession(trace_id) as session:
        with span("qa.retrieve", question_len=8):
            time.sleep(0.01)
        with span("qa.generate", hits=2):
            time.sleep(0.02)
        with span("qa.rerank", status_check=True) as record:
            record.status = "error"
    session.flush()


async def test_trace_read_from_redis(client: AsyncClient, redis_clean) -> None:
    trace_id = new_trace_id()
    _write_trace(trace_id)

    response = await client.get(f"/api/v1/traces/{trace_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "redis"
    names = [item["span"] for item in body["spans"]]
    assert names == ["qa.retrieve", "qa.generate", "qa.rerank"]
    assert body["summary"]["span_count"] == 3
    assert body["summary"]["error_count"] == 1
    assert body["summary"]["slowest"]["span"] == "qa.generate"


async def test_trace_falls_back_to_mysql(client: AsyncClient, redis_clean) -> None:
    trace_id = new_trace_id()
    _write_trace(trace_id)
    redis_clean.delete(f"trace:{trace_id}")  # 模拟缓存过期

    response = await client.get(f"/api/v1/traces/{trace_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source"] == "mysql"
    assert len(body["spans"]) == 3
    assert body["spans"][0]["attrs"] == {"question_len": 8}


async def test_unknown_trace_returns_404(client: AsyncClient) -> None:
    response = await client.get(f"/api/v1/traces/{'f' * 32}")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
