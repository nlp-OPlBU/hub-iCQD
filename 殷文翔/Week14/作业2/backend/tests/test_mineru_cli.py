import json
import subprocess
import zipfile

import pytest

from app.core.config import Settings
from app.services.chunking import build_chunks
from app.services.mineru_artifacts import load_artifacts
from app.services.mineru_cli import MineruError, MineruRunner, safe_extract


def artifact_zip(path, *, image=True):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('markdown.md', '商品可以添加100件')
        z.writestr('structured_content.json', json.dumps({'pages': [{'page_idx': 0, 'blocks': [
            {'type': 'text', 'content': '商品可以添加100件'},
            {'type': 'image', 'content': '', 'image_source': 'images/a.jpg'},
        ]}]}))
        if image:
            z.writestr('images/a.jpg', b'image')


def test_valid_artifacts_and_bounded_deterministic_sources(tmp_path):
    settings = Settings(_env_file=None)
    archive = tmp_path / 'output.zip'
    artifact_zip(archive)
    safe_extract(archive, tmp_path / 'parsed', settings)
    artifact = load_artifacts(tmp_path / 'parsed', 'parsed/d/v1')
    kwargs = dict(document_id='d', version=1, filename='sample.pdf', artifact=artifact, max_chars=3)
    chunks = build_chunks(**kwargs)
    assert chunks == build_chunks(**kwargs)
    assert all(c['page'] == 1 and len(c['text']) <= 3 for c in chunks)
    assert chunks[0]['page_image_keys'] == ['parsed/d/v1/images/a.jpg']
    assert ''.join(c['text'] for c in chunks) == '商品可以添加100件'


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'C:/drive', 'a/../../escape'])
def test_unsafe_zip(tmp_path, name):
    archive = tmp_path / 'output.zip'
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr(name, 'bad')
    with pytest.raises(MineruError, match='不安全'):
        safe_extract(archive, tmp_path / 'out', Settings(_env_file=None))


def test_corrupt_zip_and_missing_image(tmp_path):
    archive = tmp_path / 'output.zip'
    archive.write_bytes(b'corrupted')
    with pytest.raises(MineruError) as err:
        safe_extract(archive, tmp_path / 'bad', Settings(_env_file=None))
    assert err.value.code == 'INVALID_ZIP'
    artifact_zip(archive, image=False)
    safe_extract(archive, tmp_path / 'parsed', Settings(_env_file=None))
    with pytest.raises(MineruError) as err:
        load_artifacts(tmp_path / 'parsed', 'parsed/d')
    assert err.value.code == 'ARTIFACT_INVALID'


def test_runner_arguments_workspace_and_timeout(tmp_path, monkeypatch):
    settings = Settings(_env_file=None, data_dir=tmp_path, mineru_timeout_seconds=1)
    runner = MineruRunner(settings)
    pdf = tmp_path / 'untrusted name ;.pdf'
    output = tmp_path / 'job' / 'out.zip'
    calls = []
    class Process:
        pid = 12345
        def __init__(self, args, **kwargs):
            calls.append((args, kwargs))
        def wait(self, timeout=None):
            if timeout:
                raise subprocess.TimeoutExpired('mineru', timeout)
    monkeypatch.setattr(subprocess, 'Popen', Process)
    killed = []
    monkeypatch.setattr('os.killpg', lambda *args: killed.append(args))
    with pytest.raises(MineruError) as err:
        runner.run(pdf, output, output.parent)
    assert err.value.code == 'MINERU_TIMEOUT' and killed
    args, options = calls[0]
    assert args[2] == str(pdf) and options['cwd'] == output.parent
    assert options.get('shell', False) is False


def test_concurrency_slot_is_released_and_bounded(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path, mineru_timeout_seconds=1)
    first, second = MineruRunner(settings), MineruRunner(settings)
    with first._slot():
        with pytest.raises(MineruError) as err:
            with second._slot():
                pytest.fail('slot exceeded')
        assert err.value.code == 'MINERU_BUSY'
    with second._slot():
        pass
