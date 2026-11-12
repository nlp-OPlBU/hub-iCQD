import httpx
import pytest

from deep_research.models import SearchResult
from deep_research.search import BochaSearchClient, MockSearchClient, SearchError


def test_parse_bocha_response(monkeypatch):
    """Bocha 响应形状：data.webPages.value，条目用 name/url/summary，date 透传。"""
    payload = {"code": 0, "data": {"webPages": {"value": [
        {"name": "标题一", "url": "https://a.com/1", "summary": "摘要一", "date": "2026-08-01"},
        {"name": "标题二", "url": "https://a.com/2", "summary": "摘要二"},
    ]}}}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    c = BochaSearchClient(api_key="sk-test")
    results = c.search("测试检索")
    assert len(results) == 2
    assert results[0] == SearchResult(
        query="测试检索", title="标题一", url="https://a.com/1",
        snippet="摘要一", date="2026-08-01")
    assert results[1].date == ""  # 缺 date 字段时为空串


def test_missing_api_key_raises():
    c = BochaSearchClient(api_key=None)
    with pytest.raises(SearchError):
        c.search("q")


def test_http_error_raises(monkeypatch):
    class _Resp:
        def raise_for_status(self):
            raise httpx.HTTPStatusError("500", request=None, response=None)

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    c = BochaSearchClient(api_key="sk-test")
    with pytest.raises(SearchError):
        c.search("q")


def test_mock_search_deterministic():
    m = MockSearchClient()
    r1 = m.search("关键词A")
    r2 = m.search("关键词A")
    assert [x.url for x in r1] == [x.url for x in r2]
    assert all(x.query == "关键词A" for x in r1)
    assert len(r1) == 3


def test_parse_bocha_null_value_returns_empty(monkeypatch):
    """Bocha 返回 value: null 时必须得到空列表而非 None（否则 pipeline 崩溃）。"""
    payload = {"code": 0, "data": {"webPages": {"value": None}}}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    assert BochaSearchClient(api_key="sk-test").search("q") == []


def test_parse_bocha_null_entries_are_filtered(monkeypatch):
    payload = {"code": 0, "data": {"webPages": {"value": [
        {"name": "有效条目", "url": "https://a.com/1", "summary": "s"},
        None,
    ]}}}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    results = BochaSearchClient(api_key="sk-test").search("q")
    assert len(results) == 1
    assert results[0].url == "https://a.com/1"
