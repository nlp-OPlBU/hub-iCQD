"""Bounded evidence chunks with offsets into normalized block text."""
from __future__ import annotations

from app.services.mineru_artifacts import evidence_type


def text_spans(text: str, max_chars: int, *, table: bool):
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        if table and end < len(text):
            # Prefer whole rows; exceptionally long rows still obey the size bound.
            boundary = text.rfind('\n', start, end)
            if boundary >= start:
                end = boundary + 1
        yield start, end
        start = end


def build_chunks(*, document_id: str, version: int, filename: str,
                 artifact: dict, max_chars: int = 1200) -> list[dict]:
    if max_chars <= 0:
        raise ValueError('max_chars must be positive')
    chunks = []
    for page in artifact['pages']:
        page_images = sorted({key for b in page['blocks'] for key in b['image_keys']})
        for block in page['blocks']:
            text = block['text']
            kind = block.get('evidence_type', evidence_type(block['type']))
            for offset, end in text_spans(text, max_chars, table=kind == 'table'):
                if not text[offset:end].strip():
                    continue
                chunks.append({
                    'chunk_id': f'{document_id}:{version}:{page["page"]}:{block["block_index"]}:{offset}',
                    'document_id': document_id, 'version': version, 'filename': filename,
                    'page': page['page'], 'block_index': block['block_index'],
                    'evidence_type': kind, 'text_origin': block.get('text_origin', 'content'),
                    'char_offset': offset, 'char_end': end, 'bbox': block['bbox'],
                    'image_keys': block['image_keys'], 'page_image_keys': page_images,
                    'chunk_index': len(chunks), 'text': text[offset:end],
                })
    return chunks
