"""Normalize observed MinerU pages into text, table and image evidence."""
from __future__ import annotations

from html.parser import HTMLParser
import json
import math
from pathlib import Path, PurePosixPath

from app.services.mineru_cli import MineruError


class TableContent(HTMLParser):
    """Keep cell/row boundaries and embedded image references, never execute HTML."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.cells, self.cell, self.images = [], [], [], []
        self.in_cell = False

    def handle_starttag(self, tag, attrs):
        if tag in ('td', 'th'):
            self.in_cell = True
            self.cell = []
        elif tag == 'br' and self.in_cell:
            self.cell.append(' ')
        elif tag == 'img':
            attrs = dict(attrs)
            if attrs.get('src'):
                self.images.append(attrs['src'])
            if self.in_cell and attrs.get('alt'):
                self.cell.append(attrs['alt'])

    def handle_data(self, data):
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.in_cell:
            self.cells.append(' '.join(''.join(self.cell).split()))
            self.in_cell = False
        elif tag == 'tr':
            if self.cells:
                self.rows.append(' | '.join(self.cells))
                self.cells = []


def evidence_type(block_type: str) -> str:
    return block_type if block_type in ('table', 'image') else 'text'


def load_artifacts(directory: Path, artifact_key: str) -> dict:
    try:
        directory = directory.resolve()
        candidates = sorted(directory.rglob('structured_content.json'))
        if len(candidates) != 1:
            raise ValueError('expected one structured_content.json')
        source = candidates[0]
        markdown = list(source.parent.glob('*.md'))
        if not markdown or not any(p.read_text(encoding='utf-8').strip() for p in markdown):
            raise ValueError('missing Markdown')
        pages = json.loads(source.read_text(encoding='utf-8'))['pages']
        if not isinstance(pages, list) or not pages:
            raise ValueError('missing pages')
        result, seen = [], set()
        for page in pages:
            idx = page['page_idx']
            if type(idx) is not int or idx < 0 or idx in seen:
                raise ValueError('invalid page index')
            seen.add(idx)
            blocks = []
            for number, block in enumerate(page['blocks']):
                content = block.get('content', '')
                if not isinstance(content, str) or not isinstance(block['type'], str):
                    raise ValueError('unsupported block content')
                kind = evidence_type(block['type'])
                annotations = {}
                for field in ('captions', 'footnotes'):
                    annotations[field] = []
                    for note in block.get(field, []):
                        value = note if isinstance(note, str) else note['content']
                        if not isinstance(value, str):
                            raise ValueError('invalid annotation')
                        annotations[field].append(value)
                table = TableContent()
                if kind == 'table':
                    table.feed(content)
                    table.close()
                text = '\n'.join(table.rows) if table.rows else content
                references = [block['image_source']] if block.get('image_source') else []
                references.extend(table.images)
                images = []
                for image in dict.fromkeys(references):
                    path = PurePosixPath(image)
                    if path.is_absolute() or '\\' in image or ':' in image or '..' in path.parts:
                        raise ValueError('unsafe image reference')
                    target = (source.parent / image).resolve()
                    target.relative_to(directory.resolve())
                    if not target.is_file() or not target.stat().st_size:
                        raise ValueError('missing image')
                    images.append(f'{artifact_key}/{target.relative_to(directory).as_posix()}')
                bbox = block.get('bbox')
                if bbox is not None and (not isinstance(bbox, list) or len(bbox) != 4 or
                    any(type(x) not in (int, float) or not math.isfinite(x) or not 0 <= x <= 1 for x in bbox)
                    or bbox[0] > bbox[2] or bbox[1] > bbox[3]):
                    raise ValueError('invalid normalized bbox')
                blocks.append({'block_index': number, 'type': block['type'], 'evidence_type': kind,
                    'content': content, **annotations, 'table_rows': table.rows,
                    'text': '\n'.join([text, *annotations['captions'], *annotations['footnotes']]).strip(),
                    'text_origin': 'content', 'bbox': bbox, 'image_keys': images})
            # An image without OCR/caption still needs its own searchable evidence.
            # Context is explicitly labelled; it is never presented as a visual description.
            for number, block in enumerate(blocks):
                if not block['text'] and block['image_keys']:
                    neighbors = [b['text'][:400] for b in blocks[max(0, number - 1):number + 2]
                                 if b['evidence_type'] == 'text' and b['text']]
                    block['text'] = ('图片证据（无文字说明）' if block['evidence_type'] == 'image' else '表格证据（无文字说明）')
                    if neighbors:
                        block['text'] += '\n同页相邻正文：' + '\n'.join(neighbors)
                    block['text_origin'] = 'page_context'
            result.append({'page': idx + 1, 'blocks': blocks})
        if seen != set(range(len(pages))):
            raise ValueError('non-contiguous page indexes')
        if not any(b['text'] for p in result for b in p['blocks']):
            raise ValueError('no searchable evidence')
        return {'schema_version': 2, 'pages': sorted(result, key=lambda p: p['page'])}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise MineruError('ARTIFACT_INVALID', 'MinerU 产物结构、页码或图片关联无效', retryable=False) from exc
