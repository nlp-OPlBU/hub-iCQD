"""Real MinerU adapters. Parsing errors are never replaced with synthetic content."""
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import httpx
from .config import DATA, PARSE_TIMEOUT

def safe_child(root, relative):
    target = (Path(root) / relative).resolve()
    if not target.is_relative_to(Path(root).resolve()):
        raise ValueError('MinerU 返回了越界文件路径')
    return target

def parse_pdf(doc_id):
    source = DATA / 'uploads' / f'{doc_id}.pdf'
    out = DATA / 'parsed' / doc_id
    out.mkdir(parents=True, exist_ok=True)
    mode = os.getenv('MINERU_MODE', 'cli')
    if mode == 'cli':
        executable = shutil.which('mineru')
        if not executable:
            raise RuntimeError('未安装 MinerU：请安装 requirements.txt，或配置 MINERU_MODE=api')
        env = dict(os.environ)
        env.setdefault('MINERU_MODEL_SOURCE', 'modelscope')
        command = [executable, '-p', str(source), '-o', str(out),
                   '-b', os.getenv('MINERU_BACKEND', 'pipeline'),
                   '-d', os.getenv('MINERU_DEVICE', 'cpu'), '-m', 'auto',
                   '--formula', os.getenv('MINERU_FORMULA_ENABLE', 'false'),
                   '--table', os.getenv('MINERU_TABLE_ENABLE', 'false'),
                   '--source', os.getenv('MINERU_MODEL_SOURCE', 'modelscope')]
        # Child output stays server-side; never expose credentials or machine paths to the browser.
        with (out / 'mineru.log').open('w') as logfile:
            result = subprocess.run(command, env=env, stdout=logfile, stderr=subprocess.STDOUT,
                                    timeout=PARSE_TIMEOUT, check=False)
        if result.returncode:
            raise RuntimeError(f'MinerU 解析失败（退出码 {result.returncode}），请检查 Worker 日志')
        candidates = list(out.rglob('*_content_list.json'))
        if not candidates:
            raise RuntimeError('MinerU 未生成 content_list.json，不能标记解析完成')
        content_file = candidates[0]
        content = json.loads(content_file.read_text())
        return normalize(doc_id, content_file.parent, content), 'MinerU 2.5.4 / pipeline'
    if mode == 'api':
        url = os.getenv('MINERU_API_URL', '').rstrip('/')
        if not url:
            raise RuntimeError('请配置 MINERU_API_URL')
        with source.open('rb') as f, httpx.Client(timeout=PARSE_TIMEOUT) as client:
            response = client.post(url + '/file_parse', files={'files': (source.name, f, 'application/pdf')},
                                   data={'backend': os.getenv('MINERU_BACKEND', 'pipeline'),
                                         'parse_method': 'auto', 'return_md': 'true',
                                         'formula_enable': os.getenv('MINERU_FORMULA_ENABLE', 'false'),
                                         'table_enable': os.getenv('MINERU_TABLE_ENABLE', 'false'),
                                         'return_content_list': 'true', 'return_images': 'true',
                                         'response_format_zip': 'false'})
            response.raise_for_status()
            body = response.json()
        entries = body.get('results', {})
        if not entries:
            raise RuntimeError('MinerU API 返回空 results')
        result = next(iter(entries.values()))
        content = result.get('content_list', [])
        if isinstance(content, str):
            content = json.loads(content)
        for name, encoded in result.get('images', {}).items():
            # MinerU 2.5.x uses bare names as image keys but img_path includes images/.
            target = safe_child(out, name if '/' in name else 'images/' + name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded.split(',')[-1], validate=True))
        (out / 'content_list.json').write_text(json.dumps(content, ensure_ascii=False, indent=2))
        (out / 'document.md').write_text(result.get('md_content', ''))
        return normalize(doc_id, out, content), 'MinerU HTTP / ' + os.getenv('MINERU_BACKEND', 'pipeline')
    raise RuntimeError('MINERU_MODE 只支持 cli 或 api')

def normalize(doc_id, root, content):
    if not isinstance(content, list):
        raise ValueError('MinerU content_list 必须是列表')
    chunks = []
    for item in content:
        kind = item.get('type', 'text')
        page = int(item.get('page_idx', 0)) + 1
        text = item.get('text', '')
        if kind == 'image':
            text = ' '.join(item.get('image_caption', []) + item.get('image_footnote', [])) or '文档插图'
        if kind == 'table':
            text = ' '.join(item.get('table_caption', [])) + '\n' + item.get('table_body', '')
            text = re.sub('<[^>]+>', ' ', text)
        if kind == 'equation':
            text = item.get('text', item.get('text_latex', ''))
        image_url = None
        relative = item.get('img_path')
        if relative:
            image_path = safe_child(root, relative)
            if image_path.is_file():
                # Convert every MinerU figure to PNG and give it a stable, non-user-controlled name.
                from PIL import Image
                target = DATA / 'parsed' / doc_id / f'figure-{len(chunks)}.png'
                with Image.open(image_path) as im:
                    im.convert('RGB').save(target)
                image_url = f'/assets/parsed/{doc_id}/{target.name}'
        if not text.strip() and not image_url:
            continue
        # Overlap preserves boundary context; image is only attached to its first chunk.
        for start in range(0, max(len(text), 1), 650):
            part = text[start:start + 800]
            chunks.append({'id': f'{doc_id}-{len(chunks)}', 'doc_id': doc_id, 'page': page,
                           'kind': kind, 'text': part, 'image_url': image_url if start == 0 else None})
    if not chunks:
        raise RuntimeError('MinerU 未解析出文本或插图，无法建立检索索引')
    return chunks
