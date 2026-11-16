"""Render a reviewable server fragment and an independent loopback Nginx config."""
import argparse
from pathlib import Path
import sys
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from app.core.config import get_settings


def quoted_path(path):
    value = str(Path(path).resolve())
    if any(char in value for char in ('"', '\\', '$', '\n', '\r', ';', '{', '}')):
        raise ValueError('Unsupported character in Nginx path')
    return '"' + value + '"'


def render(output, *, static_root=None, port=18080, api_origin=None, max_body=None, timeout=None):
    settings = get_settings()
    static_root = Path(static_root or ROOT / 'frontend/dist')
    static_root = (ROOT / static_root).resolve() if not static_root.is_absolute() else static_root.resolve()
    if not (static_root / 'index.html').is_file():
        raise ValueError('Build frontend/dist first')
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    api_origin = api_origin or f'http://{settings.app_host}:{settings.app_port}'
    url = urlsplit(api_origin)
    if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost')
            or not url.port or url.path or url.query or url.fragment or url.username):
        raise ValueError('API origin must be a loopback HTTP origin with a port')
    output = Path(output)
    output = (ROOT / output).resolve() if not output.is_absolute() else output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    limit = max_body if max_body is not None else max(settings.upload_max_request_bytes,
        settings.image_max_upload_files * settings.image_max_file_bytes + 65536)
    wait = timeout if timeout is not None else settings.llm_timeout_seconds * 2 + 30
    if limit <= 0 or wait <= 0:
        raise ValueError('Body limit and timeout must be positive')
    template = (ROOT / 'infra/nginx/multimodal-rag.conf').read_text(encoding='utf-8')
    for key, value in {'STATIC_ROOT': quoted_path(static_root), 'PORT': str(port),
                       'API_ORIGIN': api_origin, 'MAX_BODY': str(limit), 'TIMEOUT': str(wait)}.items():
        template = template.replace('__' + key + '__', value)
    fragment = output / 'server.conf'
    fragment.write_text(template, encoding='utf-8')
    # No user/global pid/log/temp paths: this config runs independently with nginx -p.
    config = output / 'nginx.conf'
    config.write_text('worker_processes 1;\npid nginx.pid;\nerror_log error.log warn;\n'
        'events { worker_connections 256; }\nhttp {\n'
        '  access_log access.log;\n  default_type application/octet-stream;\n'
        '  types { text/html html; text/css css; application/javascript js; '
        'application/json json; image/png png; image/jpeg jpg jpeg; image/svg+xml svg; }\n'
        '  client_body_temp_path client_temp;\n  proxy_temp_path proxy_temp;\n'
        f'  include {quoted_path(fragment)};\n}}\n', encoding='utf-8')
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'runtime/nginx')
    parser.add_argument('--static-root', type=Path)
    parser.add_argument('--port', type=int, default=18080)
    parser.add_argument('--api-origin')
    args = parser.parse_args()
    print(render(args.output_dir, static_root=args.static_root, port=args.port, api_origin=args.api_origin))
