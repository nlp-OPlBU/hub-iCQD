"""配置：凭据校验、DSN 转义、路径锚点。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import PROJECT_ROOT, Settings, resolve_path


def test_dsn_escapes_password() -> None:
    settings = Settings(_env_file=None, mysql={"password": "p@ss:word/1"})
    dsn = settings.mysql.dsn()
    assert "p%40ss%3Aword%2F1" in dsn
    assert "mysql+pymysql://root:" in dsn


def test_missing_password_fails_fast() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, mysql={"password": ""})


def test_relative_paths_anchor_to_project_root() -> None:
    path = resolve_path("data/raw")
    assert path.is_absolute()
    assert path == PROJECT_ROOT / "data" / "raw"


def test_nested_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYSQL__PASSWORD", "secret")
    monkeypatch.setenv("KAFKA__TOPIC", "doc.ingest.custom")
    settings = Settings(_env_file=None)
    assert settings.mysql.password == "secret"
    assert settings.kafka.topic == "doc.ingest.custom"


def test_cloud_llm_requires_api_key() -> None:
    with pytest.raises(ValidationError, match="VLLM__API_KEY"):
        Settings(_env_file=None, mysql={"password": "x"}, vllm={"provider": "cloud"})


def test_cloud_llm_accepts_key() -> None:
    settings = Settings(
        _env_file=None,
        mysql={"password": "x"},
        vllm={"provider": "cloud", "api_key": "sk-x", "model": "deepseek-flash"},
    )
    assert settings.vllm.is_cloud is True
    assert settings.vllm.model == "deepseek-flash"
