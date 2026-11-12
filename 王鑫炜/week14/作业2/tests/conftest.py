import pytest
from app import storage, parser, llm, main

@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    for module in (storage, parser, llm, main):
        monkeypatch.setattr(module, 'DATA', tmp_path)
    for folder in ('uploads', 'parsed', 'questions'):
        (tmp_path / folder).mkdir()
    storage.init()
    return tmp_path
