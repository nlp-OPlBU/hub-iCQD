"""Gracefully stop only this project's application and private MySQL; retain all models."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from dataclasses import dataclass
import errno
import json
import math
import os
from pathlib import Path
import platform
import select
import shutil
import signal
import sys
import time
from typing import Callable

from start_project import ROOT, module_pids, nginx_pids, project_path, startup_lock, tcp_open


class UnsafeTarget(RuntimeError):
    """A process cannot be proved to belong to this deployment."""


def process_stat(pid: int) -> tuple[str, int, str]:
    # comm may contain spaces and parentheses. Fields after its last ')' start at state.
    fields = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
    return fields[19], int(fields[1]), fields[0]  # starttime, ppid, state


def process_args(pid: int) -> list[str]:
    return [os.fsdecode(value) for value in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0') if value]


def executable_is(pid: int, executable: Path) -> bool:
    try:
        return Path(f'/proc/{pid}/exe').resolve(strict=True) == executable.resolve(strict=True)
    except (OSError, RuntimeError):
        return False


def app_pids(module: str, env_file: Path) -> list[int]:
    return [pid for pid in module_pids(module, env_file)
            if executable_is(pid, Path(sys.executable)) and process_args(pid)[1:3] == ['-m', module]]


def option(args: list[str], name: str) -> str | None:
    values = [arg.split('=', 1)[1] for arg in args if arg.startswith(name + '=')]
    values += [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == name]
    return values[0] if len(values) == 1 else None


def private_mysql_pids(settings, directory: Path, binary: Path) -> list[int]:
    if settings.mysql_host != '127.0.0.1' or settings.mysql_port == 3306:
        return []  # Shared/remote MySQL is outside the stop whitelist.
    marker = directory / 'project-mysql.json'
    if not marker.is_file():
        if tcp_open(settings.mysql_host, settings.mysql_port):
            raise UnsafeTarget('mysql：端口在线但缺少项目实例标记，保留该服务')
        return []
    metadata = {'port': settings.mysql_port, 'database': settings.mysql_database, 'user': settings.mysql_user}
    if json.loads(marker.read_text(encoding='utf-8')) != metadata:
        raise UnsafeTarget('mysql：项目实例标记与所选配置不匹配，保留该服务')
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        pid = int(proc.name)
        if not executable_is(pid, binary):
            continue
        try:
            args = process_args(pid)
            if ('--no-defaults' in args and option(args, '--port') == str(settings.mysql_port)
                    and option(args, '--bind-address') == '127.0.0.1'
                    and option(args, '--datadir') == str(directory / 'data')
                    and option(args, '--pid-file') == str(directory / 'mysql.pid')):
                found.append(pid)
        except FileNotFoundError:
            continue
    if not found and tcp_open(settings.mysql_host, settings.mysql_port):
        raise UnsafeTarget('mysql：端口在线但进程/数据目录无法确认，保留该服务')
    return found


def supervised(pid: int) -> bool:
    """Legacy run_project may force kill children after 60s; do not trigger that path."""
    seen = set()
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            pid = process_stat(pid)[1]
            args = process_args(pid)
            cwd = Path(f'/proc/{pid}/cwd').resolve()
            if any((cwd / arg).resolve() == ROOT / 'scripts/run_project.py' for arg in args[1:]
                   if arg and not arg.startswith('-')):
                return True
        except FileNotFoundError:
            break
    return False


def open_pidfd(pid: int) -> int:
    if hasattr(os, 'pidfd_open'):
        return os.pidfd_open(pid)
    # The current conda Python/glibc do not export pidfd helpers. These syscall
    # numbers are shared by Linux x86_64/aarch64; never fall back to os.kill(PID).
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'aarch64'):
        raise UnsafeTarget('当前平台缺少 pidfd 支持，未发送任何信号')
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(434), ctypes.c_int(pid), ctypes.c_uint(0))
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    os.set_inheritable(result, False)
    return result


def send_signal(fd: int, signum: int):
    if hasattr(signal, 'pidfd_send_signal'):
        signal.pidfd_send_signal(fd, signum)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.syscall(ctypes.c_long(424), ctypes.c_int(fd), ctypes.c_int(signum),
                          ctypes.c_void_p(), ctypes.c_uint(0))
    if result < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


@dataclass
class Target:
    name: str
    find: Callable[[], list[int]]
    signum: int = signal.SIGTERM
    # An occupied port with no verifiable project process must not be called stopped.
    occupied: Callable[[], bool] = lambda: False
    before_stop: Callable[[], None] = lambda: None


@dataclass
class PinnedProcess:
    pid: int
    birth: str
    fd: int

    def exited(self) -> bool:
        return bool(select.select([self.fd], [], [], 0)[0])


@contextmanager
def pin_targets(targets: list[Target]):
    """Validate all targets before the first signal, then use stable kernel handles."""
    pinned: dict[str, list[PinnedProcess]] = {}
    try:
        for target in targets:
            pinned[target.name] = []
            for pid in target.find():
                fd = None
                try:
                    birth, _, state = process_stat(pid)
                    if state == 'Z':
                        continue
                    fd = open_pidfd(pid)
                    if process_stat(pid)[0] != birth or pid not in target.find():
                        raise UnsafeTarget(f'{target.name}：进程身份变化，未发送停止信号')
                    if supervised(pid):
                        raise UnsafeTarget(f'{target.name}：由 run_project.py 监督，停止子进程会触发其强杀逻辑；'
                                           '本脚本未发送信号，请先安排该监督入口的停机')
                    pinned[target.name].append(PinnedProcess(pid, birth, fd))
                    fd = None
                except (FileNotFoundError, ProcessLookupError):
                    continue
                finally:
                    if fd is not None:
                        os.close(fd)
            if not pinned[target.name] and target.occupied():
                raise UnsafeTarget(f'{target.name}：端口仍被占用，无法确认项目进程，未发送停止信号')
        yield pinned
    finally:
        for processes in pinned.values():
            for process in processes:
                os.close(process.fd)


def stop_targets(targets: list[Target], *, timeout: float, preview: bool = False) -> int:
    with pin_targets(targets) as pinned:
        for target in targets:
            processes = pinned[target.name]
            if not processes:
                print(f'[未启动，跳过] {target.name}', flush=True)
                continue
            ids = ','.join(str(process.pid) for process in processes)
            if preview:
                print(f'[将停止] {target.name}：PID={ids}，{signal.Signals(target.signum).name}', flush=True)
                continue
            target.before_stop()
            # Recheck immediately before signalling; pidfd prevents PID reuse races.
            for process in processes:
                if process.exited():
                    continue
                try:
                    unchanged = process.pid in target.find() and process_stat(process.pid)[0] == process.birth
                except (FileNotFoundError, ProcessLookupError):
                    if process.exited():
                        continue
                    raise
                if not unchanged:
                    if process.exited():
                        continue
                    raise UnsafeTarget(f'{target.name}：进程身份变化，保留进程及后续模块')
                try:
                    send_signal(process.fd, target.signum)
                except ProcessLookupError:
                    pass
            print(f'[正在停止] {target.name}：PID={ids}，等待当前请求/任务收尾', flush=True)
            deadline = time.monotonic() + timeout
            next_notice = time.monotonic() + 15
            while any(not process.exited() for process in processes):
                if time.monotonic() >= deadline:
                    print(f'[停止超时] {target.name}：未强杀；不再停止后续模块，请排查后重试', flush=True)
                    return 1
                if time.monotonic() >= next_notice:
                    print(f'[等待退出] {target.name}…', flush=True)
                    next_notice = time.monotonic() + 15
                time.sleep(.1)
            if target.find() or target.occupied():
                print(f'[停止未完成] {target.name}：仍有进程或监听端口，保留后续模块', flush=True)
                return 1
            print(f'[已安全停止] {target.name}', flush=True)
    print('预览完成，未发送停止信号。' if preview else '项目应用及私有数据库已停止；模型与共享服务保留运行。', flush=True)
    return 0


def build_targets(settings, args) -> list[Target]:
    nginx = Path(shutil.which('nginx') or '/nonexistent-nginx')
    host = '127.0.0.1' if settings.app_host == '0.0.0.0' else settings.app_host
    targets = [Target('nginx', lambda: [pid for pid in nginx_pids(args.nginx_config)
                                      if executable_is(pid, nginx)], signal.SIGQUIT,
                      occupied=lambda: tcp_open('127.0.0.1', args.web_port))]
    for name, module in [('api', 'app.main'), ('dispatcher', 'app.workers.outbox_dispatcher'),
                         ('worker', 'app.workers.pdf_consumer')]:
        targets.append(Target(name, lambda module=module: app_pids(module, args.env_file),
                              occupied=(lambda: tcp_open(host, settings.app_port)) if name == 'api' else lambda: False))

    def require_apps_stopped():
        modules = ('app.main', 'app.workers.outbox_dispatcher', 'app.workers.pdf_consumer')
        for proc in Path('/proc').glob('[0-9]*'):
            try:
                cmd = process_args(int(proc.name))
                if (len(cmd) > 2 and cmd[1] == '-m' and cmd[2] in modules
                        and proc.joinpath('cwd').resolve() == ROOT / 'backend'):
                    raise UnsafeTarget('mysql：当前项目仍有应用进程（可能使用另一配置），保留数据库')
            except FileNotFoundError:
                continue

    if settings.mysql_host == '127.0.0.1' and settings.mysql_port != 3306:
        targets.append(Target('mysql', lambda: private_mysql_pids(settings, args.mysql_directory, args.mysql_binary),
            occupied=lambda: tcp_open(settings.mysql_host, settings.mysql_port), before_stop=require_apps_stopped))
    else:
        print('[保留] MySQL：当前配置为共享/远程实例，不在停止范围', flush=True)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_env = os.environ.get('RAG_ENV_FILE') or (
        'runtime/deployment/project.env' if (ROOT / 'runtime/deployment/project.env').is_file() else '.env')
    parser.add_argument('--env-file', type=project_path, default=project_path(default_env))
    parser.add_argument('--check', '--dry-run', dest='preview', action='store_true', help='仅预览目标，不发送停止信号')
    parser.add_argument('--timeout', type=float, help='每个模块的优雅退出等待秒数；默认 MinerU 超时 + 120 秒')
    parser.add_argument('--mysql-directory', type=project_path, default=ROOT / 'runtime/deployment/mysql')
    parser.add_argument('--mysql-binary', type=project_path, default=Path('/usr/sbin/mysqld'))
    parser.add_argument('--nginx-config', type=project_path, default=ROOT / 'runtime/deployment/nginx/nginx.conf')
    parser.add_argument('--web-port', type=int, default=18080)
    args = parser.parse_args()
    if not args.env_file.is_file():
        parser.error('配置文件不存在，请使用 --env-file 指定项目部署配置')
    if args.timeout is not None and (not math.isfinite(args.timeout) or args.timeout <= 0):
        parser.error('timeout 必须为有限正数')
    if not 1 <= args.web_port <= 65535:
        parser.error('web-port 必须在 1..65535')
    if not all(path.is_relative_to(ROOT) for path in (args.mysql_directory, args.nginx_config)):
        parser.error('MySQL 目录和 Nginx 配置须位于当前项目内')
    os.environ['RAG_ENV_FILE'] = str(args.env_file)
    from app.core.config import get_settings
    try:
        get_settings.cache_clear()
        settings = get_settings()
        timeout = args.timeout if args.timeout is not None else settings.mineru_timeout_seconds + 120
        print(f'配置：{args.env_file}\n模式：' + ('只读预览' if args.preview else '安全停止'), flush=True)
        print('[保留] 所有 vLLM/MinerU/Embedding/Reranker 模型服务及共享 Kafka、ES、Redis、Nginx', flush=True)
        targets = build_targets(settings, args)
        if args.preview:
            return stop_targets(targets, timeout=timeout, preview=True)
        with startup_lock(ROOT / 'runtime/start-project.lock'):
            return stop_targets(targets, timeout=timeout)
    except KeyboardInterrupt:
        print('\n停止检查已中断，未强杀；已收到信号的模块可能仍在收尾，请重新预览。', flush=True)
        return 130
    except (UnsafeTarget, RuntimeError) as exc:
        print(f'[拒绝停止] {exc}', file=sys.stderr)
        return 1
    except OSError as exc:
        reason = '当前系统不支持安全进程句柄' if exc.errno in (errno.ENOSYS, errno.EINVAL) else '权限不足或进程检查失败'
        print(f'[停止未完成] {reason}；未强杀，保留后续模块。', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'[停止未完成] 配置或进程检查失败（{type(exc).__name__}），保留后续模块。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
