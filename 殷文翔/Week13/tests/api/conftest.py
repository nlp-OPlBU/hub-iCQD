"""API 测试使用临时库和工作流替身，不请求外部模型。"""

import sqlite3
from contextlib import closing
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

import sql_agent.api.app as app_module
from sql_agent.models import WorkflowOutput


@pytest.fixture
def api_config(tmp_path, monkeypatch):
    db_path = tmp_path / "api.db"
    with closing(sqlite3.connect(db_path)) as connection:
        connection.executescript("""
            CREATE TABLE tracks (TrackId INTEGER PRIMARY KEY, Name TEXT);
            INSERT INTO tracks VALUES (1, 'A'), (2, 'B');
        """)
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, db_path=db_path))
    # 保留 pytest 日志捕获；生产生命周期仍调用真实日志初始化。
    monkeypatch.setattr(app_module, "configure_logging", lambda: None)
    return db_path


@pytest.fixture
def api_workflow():
    return SimpleNamespace(run=AsyncMock(return_value=WorkflowOutput(
        question="列出歌曲", answer="A、B", sql="SELECT Name FROM tracks",
        tables=["tracks"], row_count=2, evidence=["A、B"],
    )))


@pytest.fixture
def api_app(api_config, api_workflow, monkeypatch):
    # 仅替换模型编排部分，仍走真实生命周期、目录创建、只读预检和资源释放。
    monkeypatch.setattr(app_module, "SQLAgentWorkflow", lambda **kwargs: api_workflow)
    return app_module.create_app()


@pytest.fixture
def client(api_app):
    with TestClient(api_app) as client:
        yield client


@pytest.fixture
def tracked_engines(monkeypatch):
    from sqlalchemy import event

    engines = []
    disposed = []
    original = app_module.create_engine

    def create_engine(*args, **kwargs):
        engine = original(*args, **kwargs)
        event.listen(engine, "engine_disposed", lambda item: disposed.append(item))
        engines.append(engine)
        return engine

    monkeypatch.setattr(app_module, "create_engine", create_engine)
    return engines, disposed
