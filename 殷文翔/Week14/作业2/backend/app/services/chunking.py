"""Deterministic, bounded chunks with physical page and block provenance."""
from __future__ import annotations


def build_chunks(*, document_id: str, version: int, filename: str,
                 artifact: dict, max_chars: int = 1200) -> list[dict]:
    if max_chars <= 0:
        raise ValueError('max_chars must be positive')
    chunks = []
    for page in artifact['pages']:
        page_images = sorted({key for b in page['blocks'] for key in b['image_keys']})
        for block in page['blocks']:
            text = block['text']
            for offset in range(0, len(text), max_chars):
                index = len(chunks)
                chunks.append({
                    'chunk_id': f'{document_id}:{version}:{page["page"]}:{block["block_index"]}:{offset}',
                    'document_id': document_id, 'version': version, 'filename': filename,
                    'page': page['page'], 'block_index': block['block_index'],
                    'char_offset': offset, 'bbox': block['bbox'],
                    'image_keys': block['image_keys'], 'page_image_keys': page_images,
                    'chunk_index': index, 'text': text[offset:offset + max_chars],
                })
    return chunks
