from uuid import UUID

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from app.api.errors import ApiError
from app.db.base import new_id
from app.models import ImageResource
from app.repositories.chat import ChatRepository
from app.repositories.images import ImageRepository
from app.schemas.chat import ImageRead
from app.services.images import image_read, inspect_image, resource_path, save_original
from app.services.rag import transaction
from app.services.upload_multipart import staged_files

router = APIRouter(tags=['images'])


def accept_images(factory, settings, conversation_id, files):
    # Decode every image before writing files or rows; the whole batch is atomic in MySQL.
    validated = []
    for file in files:
        data = file.file.read(settings.image_max_file_bytes + 1)
        if not data or len(data) > settings.image_max_file_bytes:
            raise ApiError(413, 'IMAGE_TOO_LARGE', '图片为空或超过文件大小限制')
        validated.append((new_id(), data, inspect_image(data, settings)))
    keys = []
    committing = False
    try:
        with transaction(factory) as session:
            ChatRepository(session).conversation(conversation_id)
            rows = []
            for image_id, data, metadata in validated:
                # Track the target before writing, including partial filesystem failures.
                key = f'uploads/chat-images/{image_id}'
                keys.append(key)
                save_original(settings, image_id, data)
                row = ImageResource(id=image_id, kind='user_upload', storage_key=key,
                                    conversation_id=conversation_id, **metadata)
                session.add(row)
                rows.append(row)
            session.flush()
            result = {'items': [image_read(row) for row in rows]}
            committing = True
        return result
    except Exception:
        if not committing:
            for key in keys:
                (settings.data_dir / key).unlink(missing_ok=True)
        else:
            # Reconfirm against the primary; never delete on a lost COMMIT acknowledgement.
            try:
                with transaction(factory) as session:
                    stored = [session.get(ImageResource, image_id) for image_id, _, _ in validated]
                    if all(row is not None and row.conversation_id == conversation_id for row in stored):
                        return {'items': [image_read(row) for row in stored]}
            except Exception:
                pass
            raise ApiError(503, 'IMAGE_UPLOAD_COMMIT_UNCERTAIN', '图片提交结果待核对，勿自动重传',
                           details={'image_ids': [image_id for image_id, _, _ in validated]}) from None
        raise


@router.post('/conversations/{conversation_id}/images', status_code=201,
    openapi_extra={'requestBody': {'required': True, 'content': {'multipart/form-data': {
        'schema': {'type': 'object', 'required': ['files'], 'properties': {
            'files': {'type': 'array', 'items': {'type': 'string', 'format': 'binary'}}}}}}}})
async def upload_images(conversation_id: UUID, request: Request):
    settings = request.app.state.settings
    try:
        async with staged_files(request, max_files=settings.image_max_upload_files,
                                max_bytes=settings.image_max_upload_files * settings.image_max_file_bytes + 65536) as files:
            return await run_in_threadpool(accept_images, request.app.state.session_factory,
                                          settings, str(conversation_id), files)
    except OSError:
        raise ApiError(503, 'STORAGE_UNAVAILABLE', '图片存储暂不可用', True) from None


@router.get('/images/{image_id}', response_model=ImageRead)
def get_image(image_id: UUID, request: Request):
    with transaction(request.app.state.session_factory) as session:
        return image_read(ImageRepository(session).get(str(image_id)))


@router.get('/images/{image_id}/content')
def get_image_content(image_id: UUID, request: Request):
    with transaction(request.app.state.session_factory) as session:
        row = ImageRepository(session).get(str(image_id))
        path = resource_path(request.app.state.settings, row.storage_key)
        media_type = row.media_type
    return FileResponse(path, media_type=media_type, headers={
        'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'private, max-age=3600',
        'Content-Disposition': 'inline'})
