"""Read-only, credential-free deployment preflight. Does not create resources."""
import importlib.metadata
import json
from pathlib import Path
import shutil
import socket
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


def main():
    settings = get_settings()
    result = {'python': sys.version.split()[0], 'dependencies': {}, 'tcp': {}, 'paths': {}}
    for package in ('fastapi', 'sqlalchemy', 'kafka-python', 'mineru', 'vllm', 'Pillow'):
        try:
            result['dependencies'][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            result['dependencies'][package] = 'missing'
    endpoints = [('mysql', settings.mysql_host, settings.mysql_port)]
    for server in settings.kafka_bootstrap_servers.split(','):
        host, port = server.strip().rsplit(':', 1)
        endpoints.append(('kafka:' + server.strip(), host, int(port)))
    for label, host, port in endpoints:
        try:
            with socket.create_connection((host, port), timeout=2):
                result['tcp'][label] = 'reachable (authentication not checked)'
        except OSError:
            result['tcp'][label] = 'unreachable'
    # Do not print URLs, credentials, response bodies or exception messages.
    result['http'] = {}
    for name, url in [('es', settings.es_url), ('llm_models', settings.llm_base_url + '/models')]:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                result['http'][name] = response.status
        except Exception:
            result['http'][name] = 'unavailable or authentication required'
    for name in ('data_dir', 'log_dir', 'mineru_home', 'mineru_config'):
        path = getattr(settings, name)
        result['paths'][name] = {'exists': path.exists()}
    result['commands'] = {cmd: bool(shutil.which(cmd)) for cmd in ('node', 'npm', 'nginx', settings.mineru_command)}
    result['retrieval_mode'] = settings.retrieval_mode
    result['vision_configured'] = settings.llm_vision_enabled
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
