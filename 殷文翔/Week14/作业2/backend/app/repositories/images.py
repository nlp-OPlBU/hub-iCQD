"""Image ownership, immutable document provenance, and resource registration."""
from __future__ import annotations

import json
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.errors import ApiError
from app.models import ImageResource, ParseTask
from app.services.images import inspect_image, resource_path


class ImageRepository:
    def __init__(self, session):
        self.session = session

    def get(self, image_id: str):
        row = self.session.get(ImageResource, image_id)
        if row is None:
            raise ApiError(404, 'IMAGE_NOT_FOUND', '图片不存在')
        return row

    def user_images(self, conversation_id: str, image_ids: list[str]):
        rows = [self.get(image_id) for image_id in image_ids]
        if any(row.kind != 'user_upload' or row.conversation_id != conversation_id for row in rows):
            raise ApiError(422, 'INVALID_IMAGE_SCOPE', '用户图片必须属于当前会话')
        return rows

    def document_images(self, settings, hit: dict):
        """Never resolve arbitrary ES file keys. Cross-check immutable MinerU metadata."""
        keys = list(dict.fromkeys([*hit.get('image_keys', []), *hit.get('page_image_keys', [])]))
        if not keys or hit.get('page') is None:
            return []
        task = self.session.scalar(select(ParseTask).where(
            ParseTask.document_id == hit['document_id'], ParseTask.version == hit['version'],
            ParseTask.status == 'completed'))
        if task is None or not task.artifact_key:
            raise ApiError(503, 'SOURCE_UNAVAILABLE', '文档来源产物不可用')
        try:
            metadata_path = resource_path(settings, task.artifact_key + '/normalized.json')
            if metadata_path.stat().st_size > settings.zip_max_entry_bytes:
                raise ValueError()
            artifact = json.loads(metadata_path.read_text(encoding='utf-8'))
            allowed = {key for page in artifact['pages'] if page['page'] == hit['page']
                       for block in page['blocks'] for key in block['image_keys']}
        except (OSError, ValueError, KeyError, TypeError, ApiError):
            raise ApiError(503, 'SOURCE_UNAVAILABLE', '文档图片来源校验失败') from None
        rows = []
        for key in keys[:settings.image_max_context_count]:
            if key not in allowed or not key.startswith(task.artifact_key + '/'):
                raise ApiError(503, 'INVALID_SOURCE_IMAGE', '索引图片与文档页码不匹配')
            row_id = str(uuid5(NAMESPACE_URL, 'multimodal-rag:' + key))
            row = self.session.get(ImageResource, row_id)
            if row is None:
                path = resource_path(settings, key)
                if path.stat().st_size > settings.image_max_file_bytes:
                    raise ApiError(413, 'IMAGE_TOO_LARGE', '文档图片超过大小限制')
                metadata = inspect_image(path.read_bytes(), settings)
                row = ImageResource(id=row_id, kind='document_image', storage_key=key,
                                    document_id=hit['document_id'], version=hit['version'],
                                    page=hit['page'], **metadata)
                try:
                    with self.session.begin_nested():
                        self.session.add(row)
                        self.session.flush()
                except IntegrityError:
                    # Concurrent questions may register the same deterministic resource.
                    row = self.session.scalar(select(ImageResource).where(ImageResource.id == row_id)
                                              .with_for_update())
                    if row is None:
                        raise
            rows.append(row)
        return rows
