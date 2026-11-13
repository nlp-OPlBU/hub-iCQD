"""Short, locked message allocation; model I/O never holds a DB transaction."""
from datetime import timedelta
import hashlib
import json

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.db.base import new_id, utcnow
from app.models import Conversation, Message
from app.repositories.document import DocumentRepository
from app.repositories.images import ImageRepository
from app.schemas.chat import AnswerResponse, ConversationRead, MessageRead
from app.schemas.common import ErrorDetail, Page
from app.services.images import image_read
from app.services.retrieval import document_scope


def request_hash(payload):
    normalized = {'text': payload.text, 'image_ids': [str(x) for x in payload.image_ids],
                  'document_ids': sorted(str(x) for x in payload.document_ids)
                  if payload.document_ids is not None else None}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class ChatRepository:
    def __init__(self, session):
        self.session = session

    def conversation(self, conversation_id, *, lock=False):
        query = select(Conversation).where(Conversation.id == conversation_id)
        row = self.session.scalar(query.with_for_update() if lock else query)
        if row is None:
            raise ApiError(404, 'CONVERSATION_NOT_FOUND', '会话不存在')
        return row

    def create(self, title):
        row = Conversation(id=new_id(), title=title)
        self.session.add(row)
        self.session.flush()
        return ConversationRead.model_validate(row)

    def conversations(self, page, page_size):
        total = self.session.scalar(select(func.count()).select_from(Conversation))
        rows = self.session.scalars(select(Conversation).order_by(Conversation.created_at.desc(),
            Conversation.id.desc()).offset((page - 1) * page_size).limit(page_size))
        return Page(items=[ConversationRead.model_validate(row) for row in rows],
                    page=page, page_size=page_size, total=total)

    def read(self, row):
        images = [image_read(ImageRepository(self.session).get(x)) for x in row.image_ids]
        error = ErrorDetail(code=row.error_code, message=row.error_message or '问答失败') if row.error_code else None
        return MessageRead(id=row.id, conversation_id=row.conversation_id, sequence=row.sequence,
                           role=row.role, status=row.status, content=row.content, images=images,
                           sources=row.sources, error=error, created_at=row.created_at,
                           updated_at=row.updated_at)

    def expire(self, row, settings):
        # Two sequential model calls, image preparation, and a safety margin.
        if row.status == 'pending' and row.updated_at < utcnow() - timedelta(
                seconds=3 * settings.llm_timeout_seconds + 120):
            row.status, row.error_code, row.error_message = (
                'failed', 'REQUEST_INTERRUPTED', '问答进程中断或超时，请用新的幂等键重试')

    def messages(self, conversation_id, page, page_size, settings):
        self.conversation(conversation_id)
        total = self.session.scalar(select(func.count()).select_from(Message).where(
            Message.conversation_id == conversation_id))
        rows = self.session.scalars(select(Message).where(Message.conversation_id == conversation_id)
            .order_by(Message.sequence).offset((page - 1) * page_size).limit(page_size).with_for_update()).all()
        for row in rows:
            self.expire(row, settings)
        self.session.flush()
        return Page(items=[self.read(row) for row in rows], page=page, page_size=page_size, total=total)

    def answer(self, user_id, assistant_id):
        return AnswerResponse(user_message=self.read(self.session.get(Message, user_id)),
                              assistant_message=self.read(self.session.get(Message, assistant_id)))

    def reserve(self, conversation_id, key, payload, settings):
        conversation = self.conversation(conversation_id, lock=True)
        rows = self.session.scalars(select(Message).where(Message.conversation_id == conversation_id,
            Message.request_key == key).order_by(Message.sequence).with_for_update()).all()
        digest = request_hash(payload)
        if rows:
            if rows[0].request_hash != digest:
                raise ApiError(409, 'IDEMPOTENCY_CONFLICT', '幂等键已用于不同问题')
            self.expire(rows[1], settings)
            self.session.flush()
            if rows[1].status == 'pending':
                raise ApiError(409, 'REQUEST_IN_PROGRESS', '问题仍在处理中')
            return rows[0].id, rows[1].id, None, False
        if settings.retrieval_mode != 'bm25' or settings.rerank_enabled:
            raise ApiError(503, 'RETRIEVAL_UNSUPPORTED', '当前问答仅支持 BM25，尚未启用向量或重排')
        if payload.image_ids and not settings.llm_vision_enabled:
            raise ApiError(503, 'VISION_NOT_READY', '视觉能力尚未启用')
        if len(payload.image_ids) > settings.image_max_upload_files:
            raise ApiError(413, 'IMAGE_COUNT_EXCEEDED', '用户图片数量超过限制')
        ImageRepository(self.session).user_images(conversation_id, [str(x) for x in payload.image_ids])
        versions = document_scope(DocumentRepository(self.session),
            [str(x) for x in payload.document_ids] if payload.document_ids is not None else None,
            index_name=settings.es_index)
        scope = [{'document_id': doc, 'version': version} for doc, version in sorted(versions.items())]
        user_id, assistant_id = new_id(), new_id()
        common = dict(conversation_id=conversation_id, request_key=key, request_hash=digest,
                      document_scope=scope)
        self.session.add_all([
            Message(id=user_id, sequence=conversation.next_sequence, role='user', status='completed',
                    content=payload.text, image_ids=[str(x) for x in payload.image_ids], **common),
            Message(id=assistant_id, sequence=conversation.next_sequence + 1, role='assistant',
                    status='pending', model=settings.llm_model, **common)])
        conversation.next_sequence += 2
        conversation.updated_at = utcnow()
        self.session.flush()
        return user_id, assistant_id, versions, True

    def finish(self, assistant_id, *, content='', sources=None, error=None):
        row = self.session.scalar(select(Message).where(Message.id == assistant_id).with_for_update())
        if row.status == 'pending':
            row.status = 'failed' if error else 'completed'
            row.content, row.sources = content, sources or []
            row.error_code = error.code if error else None
            row.error_message = error.message if error else None
            self.session.flush()
