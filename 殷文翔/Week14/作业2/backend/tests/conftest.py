"""Opt-in real MySQL tests: a temporary private instance; never the .env database.

Set RAG_TEST_MYSQL_BINARY to a local mysqld executable to enable.
"""
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time

import pymysql
import pytest
from app.core.config import Settings
from app.db.session import create_db_engine


@pytest.fixture(scope='session')
def isolated_mysql(tmp_path_factory):
    binary = os.environ.get('RAG_TEST_MYSQL_BINARY')
    if not binary:
        pytest.skip('Set RAG_TEST_MYSQL_BINARY to test a temporary real MySQL instance')
    work = tmp_path_factory.mktemp('rag_mysql')
    data = work / 'data'
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    common = [binary, '--no-defaults', f'--basedir={Path(binary).resolve().parent.parent}', f'--datadir={data}']
    if os.name == 'posix' and os.geteuid() == 0:
        common.append('--user=root')  # Only the explicitly opted-in, temporary test instance.
    with (work / 'init.log').open('wb') as log:
        subprocess.run([*common, '--initialize-insecure', '--console'], stdout=log, stderr=log,
                       check=True, timeout=120, creationflags=flags)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    process = None
    engine = None
    password = secrets.token_urlsafe(24)
    with (work / 'server.log').open('wb') as log:
        try:
            process = subprocess.Popen([*common, '--console', '--bind-address=127.0.0.1',
                f'--port={port}', '--mysqlx=OFF', '--skip-log-bin', '--innodb-buffer-pool-size=32M'],
                stdout=log, stderr=log, creationflags=flags)
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f'Temporary MySQL stopped; inspect {work / "server.log"}')
                try:
                    conn = pymysql.connect(host='127.0.0.1', port=port, user='root', connect_timeout=1, autocommit=True)
                    break
                except pymysql.err.OperationalError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.25)
            with conn:
                with conn.cursor() as cur:
                    cur.execute('CREATE DATABASE rag_migration_test CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci')
                    cur.execute("ALTER USER 'root'@'localhost' IDENTIFIED BY %s", (password,))
            settings = Settings(_env_file=None, mysql_host='127.0.0.1', mysql_port=port,
                                mysql_database='rag_migration_test', mysql_user='root', mysql_password=password)
            engine = create_db_engine(settings)
            yield engine, settings
        finally:
            if engine is not None:
                engine.dispose()
            if process is not None and process.poll() is None:
                try:
                    with pymysql.connect(host='127.0.0.1', port=port, user='root', password=password, connect_timeout=2) as conn:
                        with conn.cursor() as cur:
                            cur.execute('SHUTDOWN')
                except pymysql.MySQLError:
                    process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
