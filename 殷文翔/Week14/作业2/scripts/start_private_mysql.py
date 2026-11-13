"""Optional dedicated native MySQL, never reads shared MySQL configuration.

Use only for an isolated project deployment when shared MySQL is unavailable.
Requires an explicit new runtime directory, non-3306 port and project credentials.
Does not install, register, start or stop any system service.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import time

import pymysql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--initialize', action='store_true')
    args = parser.parse_args()
    settings = get_settings()
    if (settings.mysql_host != '127.0.0.1' or settings.mysql_port == 3306
            or settings.mysql_user in ('', 'root') or not settings.mysql_password.get_secret_value()):
        parser.error('Requires MYSQL_HOST=127.0.0.1, non-3306 port, non-root user and password')
    if not settings.mysql_database.replace('_', '').isalnum():
        parser.error('Use an alphanumeric/underscore project database name')
    work = (ROOT / args.directory).resolve() if not args.directory.is_absolute() else args.directory.resolve()
    with socket.socket() as check:
        check.bind(('127.0.0.1', settings.mysql_port))
    marker = work / 'project-mysql.json'
    metadata = {'port': settings.mysql_port, 'database': settings.mysql_database,
                'user': settings.mysql_user}
    if args.initialize:
        work.mkdir(parents=True, exist_ok=False)
        work.chmod(0o700)
    elif not marker.is_file() or json.loads(marker.read_text()) != metadata:
        parser.error('Missing/mismatched project instance marker; refusing to use this directory')
    binary = args.binary.resolve(strict=True)
    common = [str(binary), '--no-defaults', f'--basedir={binary.parent.parent}',
              f'--datadir={work / "data"}']
    if os.geteuid() == 0:
        common.append('--user=root')
    if args.initialize:
        with (work / 'initialize.log').open('wb') as log:
            subprocess.run([*common, '--initialize-insecure'], stdout=log, stderr=log,
                           check=True, timeout=120)
    socket_tag = hashlib.sha256(str(work).encode()).hexdigest()[:16]
    process = subprocess.Popen([*common, '--bind-address=127.0.0.1',
        f'--port={settings.mysql_port}', f'--socket=/tmp/rag-mysql-{socket_tag}.sock',
        f'--pid-file={work / "mysql.pid"}', f'--log-error={work / "mysql.log"}',
        '--mysqlx=OFF', '--skip-log-bin', '--innodb-buffer-pool-size=128M'])
    def shutdown(*_):
        if process.poll() is None:
            process.terminate()
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        if args.initialize:
            deadline = time.monotonic() + 60
            while True:
                if process.poll() is not None:
                    raise RuntimeError('Private MySQL stopped; inspect its project log')
                try:
                    if not (work / 'mysql.pid').is_file():
                        time.sleep(.1)
                        if time.monotonic() > deadline:
                            raise RuntimeError('Private MySQL did not write its PID')
                        continue
                    if int((work / 'mysql.pid').read_text()) != process.pid:
                        raise RuntimeError('Private MySQL PID mismatch')
                    conn = pymysql.connect(host='127.0.0.1', port=settings.mysql_port,
                        user='root', connect_timeout=1, autocommit=True)
                    break
                except pymysql.err.OperationalError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(.25)
            admin_password = secrets.token_urlsafe(32)
            # Persist the recovery credential before changing authentication; never print it.
            fd = os.open(work / 'admin.secret', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, 'w') as handle:
                handle.write(admin_password)
            with conn, conn.cursor() as cursor:
                cursor.execute('ALTER USER \'root\'@\'localhost\' IDENTIFIED BY %s', (admin_password,))
                cursor.execute(f'CREATE DATABASE `{settings.mysql_database}` CHARACTER SET utf8mb4')
                cursor.execute('CREATE USER %s@\'localhost\' IDENTIFIED BY %s',
                    (settings.mysql_user, settings.mysql_password.get_secret_value()))
                cursor.execute(f'GRANT ALL ON `{settings.mysql_database}`.* TO %s@\'localhost\'',
                               (settings.mysql_user,))
            marker.write_text(json.dumps(metadata), encoding='utf-8')
        print('Private project MySQL running on loopback; Ctrl-C/SIGTERM stops only this instance.', flush=True)
        return process.wait()
    finally:
        shutdown()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == '__main__':
    raise SystemExit(main())
