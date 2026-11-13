from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request

from app.api.errors import ApiError
from app.repositories.chat import ChatRepository
from app.repositories.document import DocumentRepository
from app.schemas.chat import (AnswerResponse, ConversationCreate, ConversationRead, MessageRead,
                              QuestionRequest, SearchHit, SearchRequest)
from app.schemas.common import Page
from app.services.elasticsearch_store import ElasticsearchError, ElasticsearchStore
from app.services.rag import answer_question, sources_for_hits, transaction
from app.services.retrieval import document_scope, search_snapshot

router = APIRouter(tags=['chat'])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.post('/conversations', response_model=ConversationRead, status_code=201)
def create_conversation(payload: ConversationCreate, request: Request):
    with transaction(request.app.state.session_factory) as session:
        return ChatRepository(session).create(payload.title)


@router.get('/conversations', response_model=Page[ConversationRead])
def list_conversations(request: Request, page: PageNumber = 1, page_size: PageSize = 20):
    with transaction(request.app.state.session_factory) as session:
        return ChatRepository(session).conversations(page, page_size)


@router.get('/conversations/{conversation_id}/messages', response_model=Page[MessageRead])
def list_messages(conversation_id: UUID, request: Request, page: PageNumber = 1, page_size: PageSize = 20):
    with transaction(request.app.state.session_factory) as session:
        return ChatRepository(session).messages(str(conversation_id), page, page_size, request.app.state.settings)


@router.post('/conversations/{conversation_id}/messages', response_model=AnswerResponse)
def ask(conversation_id: UUID, payload: QuestionRequest, request: Request,
        idempotency_key: Annotated[UUID, Header(alias='Idempotency-Key')]):
    return answer_question(request.app.state.session_factory, request.app.state.settings,
                           str(conversation_id), str(idempotency_key), payload)


@router.post('/search', response_model=list[SearchHit])
def search(payload: SearchRequest, request: Request):
    settings, factory = request.app.state.settings, request.app.state.session_factory
    if settings.retrieval_mode != 'bm25' or settings.rerank_enabled:
        raise ApiError(503, 'RETRIEVAL_UNSUPPORTED', '当前仅支持 BM25 检索')
    with transaction(factory) as session:
        versions = document_scope(DocumentRepository(session),
            [str(x) for x in payload.document_ids] if payload.document_ids is not None else None,
            index_name=settings.es_index)
    try:
        hits = search_snapshot(ElasticsearchStore(settings), payload.text, versions, payload.limit)
    except ElasticsearchError:
        raise ApiError(503, 'SEARCH_UNAVAILABLE', '检索服务暂不可用', True) from None
    sources, _ = sources_for_hits(factory, settings, hits)
    return [SearchHit(text=hit['text'], score=hit.get('score') or 0, source=source)
            for hit, source in zip(hits, sources)]
