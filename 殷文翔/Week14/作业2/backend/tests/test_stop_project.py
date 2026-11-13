"""Stop only isolated fixture processes; never signal deployment or model services."""
import importlib.util
import json
import os
from pathlib import Path
import signal
from types import SimpleNamespace
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('project_stopper', ROOT / 'scripts/stop_project.py')
stopper = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = stopper
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC.loader.exec_module(stopper)


@pytest.fixture
def children(tmp_path):
    processes = []

    def launch(name, *, ignore=False, cwd=None, command=None, env=None):
        ready = tmp_path / f'{name}.ready'
        done = tmp_path / f'{name}.done'
        code = (
            'import signal,time\nfrom pathlib import Path\n'
            'def finish(*args):\n'
            ' time.sleep(.15)\n'
            f' Path({str(done)!r}).write_text("flushed")\n'
            ' raise SystemExit(0)\n'
            f'signal.signal(signal.SIGTERM, {"signal.SIG_IGN" if ignore else "finish"})\n'
            f'Path({str(ready)!r}).touch()\n'
            'while True: time.sleep(.05)\n')
        process = subprocess.Popen(command or [sys.executable, '-c', code], cwd=cwd, env=env)
        processes.append(process)
        if command is None:
            deadline = time.monotonic() + 5
            while not ready.exists():
                assert process.poll() is None
                assert time.monotonic() < deadline
                time.sleep(.01)
        return process, done

    yield launch
    for child in processes:
        if child.poll() is None:
            child.kill()  # Only fixture-owned subprocesses; production stopper has no force path.
        child.wait(timeout=5)


def live(child):
    return lambda: [child.pid] if child.poll() is None else []


def test_graceful_shutdown_waits_for_flush_leaves_model_and_skips_on_repeat(children, capsys):
    worker, worker_done = children('worker')
    database, db_done = children('database')
    model, model_done = children('model')

    def require_worker_done():
        assert worker_done.read_text() == 'flushed'
        assert worker.poll() == 0

    targets = [stopper.Target('worker', live(worker)),
               stopper.Target('mysql', live(database), before_stop=require_worker_done)]
    assert stopper.stop_targets(targets, timeout=3) == 0
    assert db_done.read_text() == 'flushed'
    assert database.poll() == 0
    assert model.poll() is None and not model_done.exists()
    assert stopper.stop_targets(targets, timeout=3) == 0
    assert '[未启动，跳过] worker' in capsys.readouterr().out


def test_timeout_never_force_kills_or_stops_database(children, capsys):
    worker, _ = children('ignoring-worker', ignore=True)
    database, db_done = children('database')
    assert stopper.stop_targets([stopper.Target('worker', live(worker)),
                                stopper.Target('mysql', live(database))], timeout=.15) == 1
    assert worker.poll() is None and database.poll() is None and not db_done.exists()
    assert '未强杀' in capsys.readouterr().out


def test_preview_sends_no_signal_and_creates_no_files(children, monkeypatch, tmp_path, capsys):
    child, done = children('preview')
    before = set(tmp_path.iterdir())

    def forbidden(*_):
        pytest.fail('Preview must not signal or call stop guards')

    monkeypatch.setattr(stopper, 'send_signal', forbidden)
    assert stopper.stop_targets([stopper.Target('worker', live(child), before_stop=forbidden)],
                                timeout=1, preview=True) == 0
    assert child.poll() is None and not done.exists()
    assert set(tmp_path.iterdir()) == before
    assert '[将停止] worker' in capsys.readouterr().out


def test_preflight_refuses_unverified_port_before_stopping_any_process(children):
    child, done = children('api')
    targets = [stopper.Target('api', live(child)),
               stopper.Target('unknown', lambda: [], occupied=lambda: True)]
    with pytest.raises(stopper.UnsafeTarget, match='无法确认项目进程'):
        stopper.stop_targets(targets, timeout=1)
    assert child.poll() is None and not done.exists()


def test_changed_identity_after_pinning_is_not_signalled(children, monkeypatch):
    child, done = children('identity')
    target = stopper.Target('worker', live(child))
    target.before_stop = lambda: setattr(target, 'find', lambda: [])
    with pytest.raises(stopper.UnsafeTarget, match='进程身份变化'):
        stopper.stop_targets([target], timeout=1)
    assert child.poll() is None and not done.exists()


def test_exited_pid_handle_does_not_signal_a_different_process(children):
    first, _ = children('first')
    survivor, _ = children('survivor')
    with stopper.pin_targets([stopper.Target('first', live(first))]) as pinned:
        fd = pinned['first'][0].fd
        first.terminate()
        first.wait(timeout=3)
        assert pinned['first'][0].exited()
        with pytest.raises(ProcessLookupError):
            stopper.send_signal(fd, signal.SIGTERM)
        assert survivor.poll() is None


def test_mysql_identity_uses_marker_executable_and_arguments_not_pid_file(children, tmp_path, monkeypatch):
    directory = tmp_path / 'mysql'
    directory.mkdir()
    settings = SimpleNamespace(mysql_host='127.0.0.1', mysql_port=13399,
                               mysql_database='isolated_test', mysql_user='test_user')
    marker = directory / 'project-mysql.json'
    metadata = {'port': settings.mysql_port, 'database': settings.mysql_database, 'user': settings.mysql_user}
    marker.write_text(json.dumps(metadata))
    monkeypatch.setattr(stopper, 'tcp_open', lambda *_: False)
    child, _ = children('mysql', command=[sys.executable, '-c', 'import time; time.sleep(60)',
        '--no-defaults', '--bind-address=127.0.0.1', '--port=13399',
        f'--datadir={directory / "data"}', f'--pid-file={directory / "mysql.pid"}'])
    # A stale/tampered PID file must never select an unrelated process.
    (directory / 'mysql.pid').write_text(str(os.getpid()))
    assert stopper.private_mysql_pids(settings, directory, Path(sys.executable)) == [child.pid]
    assert stopper.private_mysql_pids(settings, directory, Path('/bin/false')) == []
    marker.write_text(json.dumps({**metadata, 'database': 'wrong'}))
    with pytest.raises(stopper.UnsafeTarget, match='实例标记'):
        stopper.private_mysql_pids(settings, directory, Path(sys.executable))
    assert child.poll() is None


def test_stop_whitelist_and_database_guard_preserve_other_config_app(children, tmp_path, monkeypatch):
    backend = tmp_path / 'backend'
    (backend / 'app').mkdir(parents=True)
    (backend / 'app/__init__.py').touch()
    (backend / 'app/main.py').write_text('import time\ntime.sleep(60)\n')
    config = tmp_path / 'selected.env'
    config.touch()
    # Simulate another configuration in this checkout using the same database.
    other, _ = children('other-api', command=[sys.executable, '-m', 'app.main'], cwd=backend,
        env={**os.environ, 'RAG_ENV_FILE': str(tmp_path / 'other.env')})
    monkeypatch.setattr(stopper, 'ROOT', tmp_path)
    monkeypatch.setattr(sys.modules['start_project'], 'ROOT', tmp_path)
    settings = SimpleNamespace(app_host='127.0.0.1', app_port=19999,
                               mysql_host='127.0.0.1', mysql_port=13399)
    args = SimpleNamespace(env_file=config, nginx_config=tmp_path / 'nginx/nginx.conf',
        web_port=19998, mysql_directory=tmp_path / 'mysql', mysql_binary=Path('/usr/sbin/mysqld'))
    targets = stopper.build_targets(settings, args)
    assert [target.name for target in targets] == ['nginx', 'api', 'dispatcher', 'worker', 'mysql']
    assert targets[0].signum == signal.SIGQUIT
    assert targets[1].find() == []  # Do not select the app belonging to the other config.
    with pytest.raises(stopper.UnsafeTarget, match='仍有应用进程'):
        targets[-1].before_stop()
    assert other.poll() is None


def test_legacy_supervisor_is_refused_before_signalling(children, monkeypatch):
    child, done = children('supervised')
    monkeypatch.setattr(stopper, 'supervised', lambda _: True)
    with pytest.raises(stopper.UnsafeTarget, match='run_project.py'):
        stopper.stop_targets([stopper.Target('worker', live(child))], timeout=1)
    assert child.poll() is None and not done.exists()
