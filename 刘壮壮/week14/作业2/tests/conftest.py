import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def local_config(monkeypatch):
    # Keep generated test DBs and uploaded fixtures inside the project, then remove them.
    artifact_root = Path(__file__).resolve().parents[1] / ".test-artifacts"
    artifact_root.mkdir(exist_ok=True)
    test_dir = Path(tempfile.mkdtemp(prefix="run-", dir=artifact_root))
    monkeypatch.setenv("RAG_ADAPTER_MODE", "local")
    monkeypatch.setenv("RAG_DATABASE_PATH", str(test_dir / "rag.sqlite3"))
    monkeypatch.setenv("RAG_STORAGE_PATH", str(test_dir / "storage"))
    from app.config import get_settings
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
    shutil.rmtree(test_dir, ignore_errors=True)
    if not any(artifact_root.iterdir()):
        artifact_root.rmdir()
