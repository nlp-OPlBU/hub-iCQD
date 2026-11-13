"""Startup behavior against isolated child processes; never start shared services."""
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('project_launcher', ROOT / 'scripts/start_project.py')
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def test_start_then_skip_existing_service_and_append_logs(tmp_path, capsys):
    # Reserve a real free port, close it before starting an isolated loopback server.
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    ready = lambda: launcher.tcp_open('127.0.0.1', port)
    log_dir = tmp_path / 'logs'
    log_dir.mkdir()
    (log_dir / 'demo.log').write_text('previous log\n')
    runner = launcher.Launcher(tmp_path / 'env', log_dir, timeout=5)
    service = launcher.Service('demo', ready,
        [sys.executable, '-m', 'http.server', str(port), '--bind', '127.0.0.1'], occupied=ready)
    try:
        assert runner.run([service]) == 0
        pid = runner.children[0].pid
        # Repeat with a new launcher, just as running start.sh again would do.
        again = launcher.Launcher(tmp_path / 'env', log_dir, timeout=5)
        assert again.run([service]) == 0
        assert not again.children
        assert runner.children[0].poll() is None and runner.children[0].pid == pid
        assert '[已启动，跳过] demo' in capsys.readouterr().out
        assert (log_dir / 'demo.log').read_text().startswith('previous log\n')
    finally:
        for process in runner.children:
            process.terminate()
            process.wait(timeout=5)


def test_occupied_unhealthy_port_is_not_replaced(tmp_path, capsys):
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        port = listener.getsockname()[1]
        runner = launcher.Launcher(tmp_path / 'env', tmp_path / 'logs', 1)
        assert not runner.ensure(launcher.Service('demo', lambda: False,
            [sys.executable, '-c', 'raise AssertionError("must not start")'],
            occupied=lambda: launcher.tcp_open('127.0.0.1', port)))
        assert not runner.children and listener.fileno() >= 0
    assert '端口已占用' in capsys.readouterr().out


def test_check_only_has_no_files_or_processes_and_reports_every_service(tmp_path, capsys):
    runner = launcher.Launcher(tmp_path / 'env', tmp_path / 'new-logs', 1, check_only=True)
    services = [launcher.Service('live', lambda: True),
        launcher.Service('offline', lambda: False, [sys.executable, '-c', 'raise AssertionError()']),
        launcher.Service('unready', lambda: False, running=lambda: [os.getpid()])]
    assert runner.run(services) == 1
    assert not runner.children and not (tmp_path / 'new-logs').exists()
    output = capsys.readouterr().out
    assert '[已启动，跳过] live' in output and '[未就绪] offline' in output
    assert '[已启动，未就绪] unready' in output


def test_failed_dependency_and_early_child_exit(tmp_path, capsys):
    runner = launcher.Launcher(tmp_path / 'env', tmp_path / 'logs', timeout=3)
    services = [launcher.Service('database', lambda: False),
        launcher.Service('worker', lambda: False, [sys.executable, '-c', 'raise AssertionError()'],
                         dependencies=('database',)),
        launcher.Service('crash', lambda: False, [sys.executable, '-c', 'raise SystemExit(7)'])]
    assert runner.run(services) == 1
    assert len(runner.children) == 1
    assert runner.children[0].returncode == 7
    output = capsys.readouterr().out
    assert '[跳过启动] worker' in output and '退出码 7' in output


def test_timeout_preserves_process_and_preexisting_start_is_not_duplicated(tmp_path, capsys):
    runner = launcher.Launcher(tmp_path / 'env', tmp_path / 'logs', timeout=.1)
    service = launcher.Service('loading', lambda: False,
        [sys.executable, '-c', 'import time; time.sleep(60)'])
    try:
        assert not runner.ensure(service)
        process = runner.children[0]
        assert process.poll() is None
        service.running = lambda: [process.pid]
        assert not runner.ensure(service)
        assert len(runner.children) == 1
        assert '不重复启动' in capsys.readouterr().out
    finally:
        for process in runner.children:
            process.terminate()
            process.wait(timeout=5)


def test_process_detection_requires_module_project_and_config(tmp_path):
    (tmp_path / 'launcher_probe.py').write_text('import time\ntime.sleep(60)\n')
    config = tmp_path / 'deployment.env'
    env = {**os.environ, 'PYTHONPATH': str(tmp_path), 'RAG_ENV_FILE': str(config)}
    child = subprocess.Popen([sys.executable, '-m', 'launcher_probe'],
                             cwd=ROOT / 'backend', env=env)
    try:
        assert child.pid in launcher.module_pids('launcher_probe', config)
        assert not launcher.module_pids('launcher_probe', tmp_path / 'other.env')
        assert not launcher.module_pids('launcher_prob', config)
    finally:
        child.terminate()
        child.wait(timeout=5)


def test_lock_blocks_concurrent_start_and_releases_after_failure(tmp_path):
    path = tmp_path / 'startup.lock'
    with launcher.startup_lock(path):
        with pytest.raises(RuntimeError, match='另一个一键启动'):
            with launcher.startup_lock(path):
                pytest.fail('Concurrent startup must not acquire lock')
    with launcher.startup_lock(path):
        pass
