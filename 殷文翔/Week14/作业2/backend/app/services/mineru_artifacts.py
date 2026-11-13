"""Validate the observed MinerU 4.x pages/blocks format without guessing pages."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from app.services.mineru_cli import MineruError


def load_artifacts(directory: Path, artifact_key: str) -> dict:
    try:
        candidates = sorted(directory.rglob('structured_content.json'))
        if len(candidates) != 1:
            raise ValueError('expected one structured_content.json')
        source = candidates[0]
        markdown = list(source.parent.glob('*.md'))
        if not markdown or not any(p.read_text(encoding='utf-8').strip() for p in markdown):
            raise ValueError('missing Markdown')
        payload = json.loads(source.read_text(encoding='utf-8'))
        pages = payload['pages']
        if not isinstance(pages, list) or not pages:
            raise ValueError('missing pages')
        result = []
        seen = set()
        for page in pages:
            idx = page['page_idx']
            if type(idx) is not int or idx < 0 or idx in seen:
                raise ValueError('invalid page index')
            seen.add(idx)
            blocks = []
            for number, block in enumerate(page['blocks']):
                text = block.get('content', '')
                if not isinstance(text, str):
                    raise ValueError('unsupported block content')
                notes = []
                for field in ('captions', 'footnotes'):
                    for note in block.get(field, []):
                        value = note if isinstance(note, str) else note['content']
                        if not isinstance(value, str):
                            raise ValueError('invalid annotation')
                        notes.append(value)
                images = []
                if image := block.get('image_source'):
                    path = PurePosixPath(image)
                    if path.is_absolute() or '\\' in image or ':' in image or '..' in path.parts:
                        raise ValueError('unsafe image reference')
                    target = (source.parent / image).resolve()
                    target.relative_to(directory.resolve())
                    if not target.is_file() or not target.stat().st_size:
                        raise ValueError('missing image')
                    images.append(f'{artifact_key}/{target.relative_to(directory).as_posix()}')
                blocks.append({'block_index': number, 'type': block['type'],
                               'text': '\n'.join([text, *notes]).strip(),
                               'bbox': block.get('bbox'), 'image_keys': images})
            result.append({'page': idx + 1, 'blocks': blocks})
        if seen != set(range(len(pages))):
            raise ValueError('non-contiguous page indexes')
        if not any(b['text'] for p in result for b in p['blocks']):
            raise ValueError('no searchable text')
        return {'schema_version': 1, 'pages': sorted(result, key=lambda p: p['page'])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MineruError('ARTIFACT_INVALID', 'MinerU 产物结构、页码或图片关联无效', retryable=False) from exc
