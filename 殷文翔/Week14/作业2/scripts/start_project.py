"""Linux one-command startup for an initialized project deployment."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass, field
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import time
from typing import Callable
from urllib.parse import urlsplit
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))


def project_path(value: str | Path) -> Path:
    return (ROOT / value).resolve()


def tcp_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def http_get(url: str, key: str = '') -> tuple[int, bytes]:
    headers = {'Authorization': 'Bearer ' + key} if key and key != 'EMPTY' else {}
    request = urllib.request.Request(url, headers=headers)
    # Local service checks must not go through a shell/user HTTP proxy.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=3) as response:
        return response.status, response.read(1024 * 1024)


def json_matches(url: str, predicate: Callable[[dict], bool], key: str = '') -> bool:
    try:
        status, body = http_get(url, key)
        value = json.loads(body)
        return status == 200 and isinstance(value, dict) and predicate(value)
    except Exception:
        return False


def module_pids(module: str, env_file: Path) -> list[int]:
    """Recognize existing processes even when launched outside this script.

    Check argv, cwd and selected configuration, not pgrep substring matches or
    stale PID files. Never print /proc environ (it can contain credentials).
    """
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            args = proc.joinpath('cmdline').read_bytes().split(b'\0')
            if not any(args[i:i + 2] == [b'-m', module.encode()] for i in range(len(args) - 1)):
                continue
            if proc.joinpath('cwd').resolve() != ROOT / 'backend':
                continue
            environment = dict(item.split(b'=', 1) for item in proc.joinpath('environ').read_bytes().split(b'\0')
                               if b'=' in item)
            selected = os.fsdecode(environment.get(b'RAG_ENV_FILE', str(ROOT / '.env').encode()))
            if project_path(selected) == env_file:
                found.append(int(proc.name))
        except (OSError, ValueError):
            continue
    return found


def nginx_pids(config: Path) -> list[int]:
    found = []
    for proc in Path('/proc').glob('[0-9]*'):
        try:
            # Nginx rewrites argv into a single process title, including -c.
            title = os.fsdecode(proc.joinpath('cmdline').read_bytes()).replace('\0', ' ').strip()
            if (title.startswith('nginx: master process ') and
                    re.search(r'(?:^| )-c ' + re.escape(str(config)) + r'(?: |$)', title)):
                found.append(int(proc.name))
        except OSError:
            continue
    return found


@contextmanager
def startup_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('另一个一键启动/停止正在执行，请等待它完成') from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@dataclass
class Service:
    name: str
    ready: Callable[[], bool]
    command: list[str] | None = None
    running: Callable[[], list[int]] = lambda: []
    occupied: Callable[[], bool] = lambda: False
    dependencies: tuple[str, ...] = ()
    prepare: Callable[[], None] = lambda: None
    hint: str = ''
    process_only: bool = False


@dataclass
class Launcher:
    env_file: Path
    log_dir: Path
    timeout: float
    check_only: bool = False
    results: dict[str, bool] = field(default_factory=dict)
    children: list[subprocess.Popen] = field(default_factory=list)

    def ensure(self, service: Service) -> bool:
        name = service.name
        if service.ready():
            state = '进程存活，业务依赖另检' if service.process_only else '就绪检查通过'
            print(f'[已启动，跳过] {name}：{state}', flush=True)
            return True
        if self.check_only:
            state = '已启动，未就绪' if service.running() else '未就绪'
            print(f'[{state}] {name}：{service.hint}', flush=True)
            return False
        if any(not self.results.get(dep, False) for dep in service.dependencies):
            print(f'[跳过启动] {name}：依赖未就绪', flush=True)
            return False
        if service.command is None:
            print(f'[无法自动启动] {name}：{service.hint}', flush=True)
            return False
        process = None
        if service.running():
            print(f'[已在运行] {name}：等待就绪，不重复启动', flush=True)
        elif service.occupied():
            print(f'[启动失败] {name}：端口已占用但就绪检查失败，不重复启动或终止占用者', flush=True)
            return False
        else:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log_path = self.log_dir / f'{name}.log'
            try:
                service.prepare()
                with log_path.open('ab') as log:
                    process = subprocess.Popen(service.command, cwd=ROOT / 'backend',
                        env={**os.environ, 'RAG_ENV_FILE': str(self.env_file)},
                        stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                        start_new_session=True)
                self.children.append(process)
            except Exception as exc:
                # Never print exception bodies, which may contain connection credentials.
                print(f'[启动失败] {name}：{type(exc).__name__}，请检查配置及 {log_path}', flush=True)
                return False
            print(f'[正在启动] {name}：PID={process.pid}，日志 {log_path}', flush=True)
        deadline = time.monotonic() + self.timeout
        next_notice = time.monotonic() + 15
        # Workers have no health endpoint: allow early initialization failures to surface.
        if service.process_only and process is not None:
            time.sleep(min(2, self.timeout))
        while True:
            if process is not None and process.poll() is not None:
                print(f'[启动失败] {name}：进程退出，退出码 {process.returncode}，请查看日志', flush=True)
                return False
            if service.ready():
                print(f'[启动成功] {name}' + ('（仅确认进程存活）' if service.process_only else ''), flush=True)
                return True
            if time.monotonic() >= deadline:
                print(f'[等待超时] {name}：尚未就绪；保留进程，可查看日志后重跑', flush=True)
                return False
            if time.monotonic() >= next_notice:
                print(f'[等待就绪] {name}…', flush=True)
                next_notice = time.monotonic() + 15
            time.sleep(.5)

    def run(self, services: list[Service]) -> int:
        for service in services:
            self.results[service.name] = self.ensure(service)
        failed = [name for name, ok in self.results.items() if not ok]
        print('\n检查完成：' + ('全部必要模块已启动。' if not failed else '未就绪：' + '、'.join(failed)), flush=True)
        return int(bool(failed))


def build_services(settings, args, env_file: Path) -> list[Service]:
    from sqlalchemy import text
    from app.db.session import create_db_engine
    from app.services.elasticsearch_store import ElasticsearchStore
    from kafka import KafkaAdminClient
    import yaml

    python = sys.executable
    engine = create_db_engine(settings)

    def mysql_ready():
        try:
            with engine.connect() as conn:
                conn.execute(text('SELECT 1'))
                return conn.execute(text('SELECT version_num FROM alembic_version')).scalars().all() == ['0001_initial']
        except Exception:
            return False
        finally:
            engine.dispose()

    def kafka_ready():
        admin = None
        try:
            admin = KafkaAdminClient(bootstrap_servers=settings.kafka_bootstrap_servers,
                request_timeout_ms=3000, bootstrap_timeout_ms=3000)
            return {settings.kafka_pdf_topic, settings.kafka_failed_topic} <= set(admin.list_topics())
        except Exception:
            return False
        finally:
            if admin is not None:
                admin.close()

    def es_ready():
        try:
            # Require an existing searchable project index. Do not create shared resources.
            store = ElasticsearchStore(settings.model_copy(update={'es_timeout_seconds': 3}))
            result = store._json('GET', f'/{settings.es_index}/_search?size=0')
            return not result.get('timed_out') and result.get('_shards', {}).get('failed', 1) == 0
        except Exception:
            return False

    services = [Service('mysql', mysql_ready,
        [python, str(ROOT / 'scripts/start_private_mysql.py'), '--directory', str(args.mysql_directory),
         '--binary', str(args.mysql_binary)]
        if settings.mysql_host == '127.0.0.1' and settings.mysql_port != 3306 else None,
        occupied=lambda: tcp_open(settings.mysql_host, settings.mysql_port),
        hint='数据库离线、认证失败或迁移不匹配；仅自动重启带 marker 的既有项目私有实例'),
        Service('kafka', kafka_ready, hint='共享 Kafka 不可用或项目 Topic 缺失，请由管理员检查'),
        Service('elasticsearch', es_ready, hint='共享 ES 或项目索引不可检索，请由管理员检查')]

    def model_service(name, url, model, script, key=''):
        endpoint = urlsplit(url)
        local = endpoint.scheme == 'http' and endpoint.hostname == '127.0.0.1' and endpoint.port
        return Service(name, lambda: json_matches(url.rstrip('/') + '/models',
            lambda value: any(item.get('id') == model for item in value.get('data', [])), key),
            [python, str(ROOT / 'scripts' / script)] if local else None,
            occupied=lambda: tcp_open(endpoint.hostname, endpoint.port or (443 if endpoint.scheme == 'https' else 80)),
            hint='模型接口离线或模型 ID 不匹配；只自动启动配置为本机 loopback 的模型')

    services.append(model_service('vlm', settings.llm_base_url, settings.llm_model,
                                 'start_vlm.py', settings.llm_api_key.get_secret_value()))
    if settings.mineru_tier == 'advanced':
        config = yaml.safe_load(settings.mineru_config.read_text(encoding='utf-8'))['model']['vlm']
        base = config['server_url'].rstrip('/')
        base = base if base.endswith('/v1') else base + '/v1'
        services.append(model_service('mineru-vlm', base, config['model'], 'start_mineru_vlm.py'))
    else:
        print('[未启用] mineru-vlm：当前解析档位 ' + settings.mineru_tier, flush=True)
    if settings.rerank_enabled:
        services.append(model_service('reranker', settings.rerank_base_url.rstrip('/') + '/v1',
            settings.rerank_model, 'start_reranker.py', settings.rerank_api_key.get_secret_value()))
    if settings.vector_enabled or settings.retrieval_mode == 'hybrid':
        services.append(model_service('embedding', settings.embedding_base_url, settings.embedding_model,
                                     'start_embedding.py', settings.embedding_api_key.get_secret_value()))
    print('[未启用] Redis：未接入主链路', flush=True)

    api_host = '127.0.0.1' if settings.app_host == '0.0.0.0' else settings.app_host
    api = f'http://{api_host}:{settings.app_port}/api'
    services.append(Service('api', lambda: json_matches(api + '/health/ready',
        lambda value: value.get('service') == settings.app_name and value.get('scope') == 'mysql_schema'),
        [python, '-m', 'app.main'], running=lambda: module_pids('app.main', env_file),
        occupied=lambda: tcp_open(api_host, settings.app_port), dependencies=('mysql',),
        hint='API 未运行或数据库未就绪'))
    worker_dependencies = ('mysql', 'kafka', 'elasticsearch', 'vlm')
    if settings.vector_enabled or settings.retrieval_mode == 'hybrid':
        worker_dependencies += ('embedding',)
    if settings.mineru_tier == 'advanced':
        worker_dependencies += ('mineru-vlm',)
    for name, module, deps in [
        ('dispatcher', 'app.workers.outbox_dispatcher', ('mysql', 'kafka')),
        ('worker', 'app.workers.pdf_consumer', worker_dependencies),
    ]:
        services.append(Service(name, lambda module=module: bool(module_pids(module, env_file)),
            [python, '-m', module], dependencies=deps, process_only=True,
            hint='未找到使用同一项目目录及配置文件的进程'))

    nginx_config = args.nginx_config
    web = f'http://127.0.0.1:{args.web_port}'

    def web_ready():
        try:
            return bool(nginx_pids(nginx_config)) and http_get(web + '/')[0] == 200 and json_matches(
                web + '/api/health/ready', lambda value: value.get('service') == settings.app_name
                and value.get('scope') == 'mysql_schema')
        except Exception:
            return False

    def prepare_nginx():
        if not nginx_config.is_file():
            from render_nginx import render
            if nginx_config.name != 'nginx.conf':
                raise ValueError('自动生成配置的文件名必须是 nginx.conf')
            render(nginx_config.parent, port=args.web_port)
        # Append validation output to its own project log; never reload shared Nginx.
        with (args.log_dir / 'nginx.log').open('ab') as log:
            subprocess.run(['nginx', '-t', '-p', str(nginx_config.parent) + '/', '-c', str(nginx_config)],
                stdout=log, stderr=subprocess.STDOUT, check=True, timeout=10)

    services.append(Service('nginx', web_ready,
        ['nginx', '-p', str(nginx_config.parent) + '/', '-c', str(nginx_config), '-g', 'daemon off;'],
        running=lambda: nginx_pids(nginx_config), occupied=lambda: tcp_open('127.0.0.1', args.web_port),
        prepare=prepare_nginx, dependencies=('api',),
        hint='项目页面或 API 代理未就绪；共享 80 端口不作为项目页面'))
    return services


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    default_env = os.environ.get('RAG_ENV_FILE') or (
        'runtime/deployment/project.env' if (ROOT / 'runtime/deployment/project.env').is_file() else '.env')
    parser.add_argument('--env-file', type=project_path, default=project_path(default_env))
    parser.add_argument('--check', action='store_true', help='只检查状态，不创建文件或启动服务')
    parser.add_argument('--timeout', type=float, default=600, help='每个模块等待就绪的秒数，默认 600')
    parser.add_argument('--mysql-directory', type=project_path, default=ROOT / 'runtime/deployment/mysql')
    parser.add_argument('--mysql-binary', type=project_path, default=Path('/usr/sbin/mysqld'))
    parser.add_argument('--nginx-config', type=project_path, default=ROOT / 'runtime/deployment/nginx/nginx.conf')
    parser.add_argument('--web-port', type=int, default=18080, help='须与既有 Nginx 配置的监听端口一致')
    parser.add_argument('--log-dir', type=project_path, default=ROOT / 'runtime/deployment/logs')
    args = parser.parse_args()
    if args.timeout <= 0 or not 1 <= args.web_port <= 65535:
        parser.error('timeout 必须为正数，web-port 必须在 1..65535')
    if not args.env_file.is_file():
        parser.error('配置文件不存在，请使用 --env-file 指定已配置的项目部署文件')
    os.environ['RAG_ENV_FILE'] = str(args.env_file)
    os.environ['PATH'] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get('PATH', '')
    # Libraries can otherwise print connection details. Launcher emits sanitized status only.
    logging.disable(logging.CRITICAL)
    from app.core.config import get_settings
    try:
        get_settings.cache_clear()
        services = build_services(get_settings(), args, args.env_file)
        launcher = Launcher(args.env_file, args.log_dir, args.timeout, args.check)
        print(f'配置：{args.env_file}\n模式：' + ('只读检查' if args.check else '启动缺失模块，已有模块跳过'), flush=True)
        if args.check:
            code = launcher.run(services)
        else:
            # One lock for this checkout, including invocations using different config files.
            with startup_lock(ROOT / 'runtime/start-project.lock'):
                code = launcher.run(services)
        print(f'项目页面：http://127.0.0.1:{args.web_port}（服务器本机）', flush=True)
        return code
    except KeyboardInterrupt:
        print('\n启动检查已中断；已启动服务保留运行，可重新执行。', flush=True)
        return 130
    except RuntimeError as exc:
        print(f'[失败] {exc}', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'[失败] 配置或环境检查失败（{type(exc).__name__}），请核对部署配置。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
