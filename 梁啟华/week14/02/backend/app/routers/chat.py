"""
多模态问答路由 - 支持纯文本问答 + 图问
"""
import base64
import mimetypes
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from loguru import logger

from ..config_loader import Config
from ..models import ChatRequest, ChatResponse, ChatMessage
from ..services.rag_service import RAGService

router = APIRouter(prefix="/api/chat", tags=["多模态问答"])

# 单例 RAG 服务
_rag_service: Optional[RAGService] = None


def get_rag() -> RAGService:
    global _rag_service
    if _rag_service is None:
        _rag_service = RAGService()
    return _rag_service


@router.post("/ask", response_model=ChatResponse)
async def ask_chat(req: ChatRequest):
    """
    多模态问答（JSON 请求）

    - question: 用户问题
    - images: Base64 data URL 列表（可选）
    - use_rag: 是否启用 RAG
    - conversation_history: 对话历史（可选）
    """
    rag = get_rag()

    history_dicts = None
    if req.conversation_history:
        history_dicts = [
            {"role": m.role, "content": m.content}
            for m in req.conversation_history
        ]

    result = await rag.answer(
        question=req.question,
        images=req.images,
        use_rag=req.use_rag,
        history=history_dicts,
    )

    response = ChatResponse(
        answer=result['answer'],
        has_images_in_answer=result.get('has_images_in_answer', False),
    )

    if result.get('sources'):
        from ..models import SourceDoc
        response.sources = [
            SourceDoc(content=s['content'], source=s['source'], score=s.get('score', 0.0))
            for s in result['sources']
        ]

    return response


@router.post("/ask-form-data")
async def ask_chat_form(
    question: str = Form(...),
    use_rag: bool = Form(True),
    images: List[UploadFile] = File(default=[]),
):
    """
    多模态问答（Form Data 版本）- 适合前端直接用 FormData 上传图片
    """
    cfg = Config.get_section('storage')
    image_dir = Path(cfg['image_dir'])
    image_dir.mkdir(parents=True, exist_ok=True)

    # 保存上传的图片并转 base64
    image_data_urls = []
    for img in images:
        content = await img.read()
        mime_type, _ = mimetypes.guess_type(img.filename or 'image.png')
        mime_type = mime_type or 'image/png'
        b64 = base64.b64encode(content).decode('utf-8')
        image_data_urls.append(f"data:{mime_type};base64,{b64}")

        # 同时保存到本地（供后续使用）
        ext = Path(img.filename or 'image.png').suffix or '.png'
        img_path = image_dir / f"{uuid.uuid4().hex}{ext}"
        img_path.write_bytes(content)

    rag = get_rag()
    result = await rag.answer(
        question=question,
        images=image_data_urls if image_data_urls else None,
        use_rag=use_rag,
    )

    response = {
        "answer": result['answer'],
        "has_images_in_answer": result.get('has_images_in_answer', False),
    }
    if result.get('sources'):
        response['sources'] = [
            {"content": s['content'], "source": s['source'], "score": s.get('score', 0.0)}
            for s in result['sources']
        ]
    if result.get('error'):
        response['error'] = result['error']

    return response


@router.get("/health")
async def health_check():
    """健康检查"""
    from ..services.vector_store import VectorStore
    try:
        vs = VectorStore()
        count = vs.count()
        return {
            "status": "ok",
            "vector_store_chunks": count,
        }
    except Exception as e:
        return {
            "status": "warning",
            "vector_store_chunks": 0,
            "note": f"vector_store not ready: {e}",
        }
