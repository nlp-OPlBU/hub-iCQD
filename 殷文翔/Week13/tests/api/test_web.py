"""同源页面和静态资源的 HTTP 回归，不调用外部模型。"""

from html.parser import HTMLParser
from uuid import UUID

import pytest

import sql_agent.api.app as app_module


class ResourceParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.resources = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        target = attributes.get("src") if tag == "script" else attributes.get("href")
        if target and target.startswith("/static/"):
            self.resources.append(target)


def test_homepage_and_referenced_resources(client, api_workflow):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-cache"
    UUID(response.headers["X-Request-ID"])
    assert '<html lang="zh-CN">' in response.text
    parser = ResourceParser()
    parser.feed(response.text)
    assert set(parser.resources) == {"/static/styles.css", "/static/main.js"}
    for resource in parser.resources:
        assert client.get(resource).status_code == 200
    api_workflow.run.assert_not_awaited()


@pytest.mark.parametrize("filename,media_types", [
    ("styles.css", ("text/css",)),
    ("main.js", ("text/javascript", "application/javascript")),
    ("api-client.js", ("text/javascript", "application/javascript")),
    ("view.js", ("text/javascript", "application/javascript")),
])
def test_static_resource_types_and_conditional_requests(client, api_workflow, filename, media_types):
    response = client.get(f"/static/{filename}")
    assert response.status_code == 200
    assert response.headers["content-type"].split(";", 1)[0] in media_types
    assert client.get(f"/static/{filename}", headers={"If-None-Match": response.headers["etag"]}).status_code == 304
    api_workflow.run.assert_not_awaited()


@pytest.mark.parametrize("path", [
    "/missing-page", "/api/v1/missing", "/static/missing.js",
    "/static/ref/chinook.db", "/static/%2e%2e/app.py", "/static/%2e%2e/%2e%2e/config.py",
    "/ref/chinook.db", "/src/sql_agent/config.py",
])
def test_unknown_paths_do_not_fall_back_to_html_or_expose_files(client, path):
    response = client.get(path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_web_routes_do_not_change_api_contract(client, api_workflow):
    paths = client.get("/openapi.json").json()["paths"]
    assert "/" not in paths
    assert "/static" not in paths
    assert "/api/v1/queries" in paths
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    assert client.get("/docs").status_code == 200
    response = client.post("/api/v1/queries", json={"question": "列出歌曲"})
    assert response.status_code == 200
    assert response.json() == api_workflow.run.return_value.model_dump()


def test_resource_paths_are_independent_of_working_directory(client, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    # 不仅请求已有应用，还在不同工作目录重新构造静态目录挂载。
    app = app_module.create_app()
    assert app is not None
    assert client.get("/").status_code == 200
    assert client.get("/static/main.js").status_code == 200
