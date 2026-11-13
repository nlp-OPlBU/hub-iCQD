"""Read immutable normalized evidence; cross-check indexed provenance."""
import json

from app.api.errors import ApiError
from app.services.chunking import build_chunks
from app.services.images import resource_path


def read_evidence(settings, task):
    if task.status != 'completed' or not task.artifact_key:
        raise ApiError(409, 'DOCUMENT_NOT_PARSED', '此版本尚无可查看的解析证据')
    try:
        path = resource_path(settings, task.artifact_key + '/normalized.json')
        if path.stat().st_size > settings.zip_max_entry_bytes:
            raise ValueError('oversized evidence')
        artifact = json.loads(path.read_text(encoding='utf-8'))
        if (artifact.get('document_id', task.document_id) != task.document_id
                or artifact.get('version', task.version) != task.version):
            raise ValueError('evidence version mismatch')
        if not isinstance(artifact['pages'], list):
            raise ValueError('invalid pages')
        return artifact
    except (OSError, ValueError, KeyError, TypeError, ApiError):
        raise ApiError(503, 'SOURCE_UNAVAILABLE', '文档来源产物不可用') from None


def verify_hit(settings, task, hit):
    """Schema 2 hits must match the actual stored block, text slice and images."""
    if not task.artifact_key and 'evidence_type' not in hit:
        return  # Legacy text-only indexes can lack stored artifacts.
    artifact = read_evidence(settings, task)
    if artifact.get('schema_version', 1) < 2 and 'evidence_type' not in hit:
        return  # Legacy indexes expose only their existing page-level provenance.
    chunks = build_chunks(document_id=hit['document_id'], version=hit['version'],
                          filename='', artifact=artifact)
    actual = next((chunk for chunk in chunks if chunk['chunk_id'] == hit['chunk_id']), None)
    fields = ('page', 'block_index', 'char_offset', 'char_end', 'bbox', 'evidence_type',
              'text_origin', 'text', 'image_keys', 'page_image_keys')
    if actual is None or any(actual[field] != hit.get(field) for field in fields):
        raise ApiError(503, 'INVALID_SOURCE_POSITION', '检索分块与原文位置不匹配')
