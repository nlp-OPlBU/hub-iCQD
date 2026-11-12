"""
数据模型 - 定义 Pydantic 模型和状态枚举
"""
import enum
from typing import List, Optional

from pydantic import BaseModel, Field


class DocumentStatus(str, enum.Enum):
    """
    文档状态机：
    pending    → 已上传，待解析
    parsing    → 正在解析
    completed  → 解析完成，已入库
    error      → 解析失败
    """
    PENDING = "pending"
    PARSING = "parsing"
    COMPLETED = "completed"
    ERROR = "error"


# ============================================
# Pydantic 模型（API 请求/响应）
# ============================================

class DocumentInfo(BaseModel):
    task_id: str
    file_name: str
    file_path: str
    file_size: int
    status: DocumentStatus
    error_message: Optional[str] = None
    created_at: str
    updated_at: str
    parsed_text_preview: Optional[str] = None


class UploadResponse(BaseModel):
    success: bool
    task_ids: List[str]
    message: str


class DocumentListResponse(BaseModel):
    total: int
    documents: List[DocumentInfo]


class StatusResponse(BaseModel):
    task_id: str
    status: DocumentStatus
    error_message: Optional[str] = None


class ChatMessage(BaseModel):
    role: str
    content: str
    images: Optional[List[str]] = None


class ChatRequest(BaseModel):
    question: str = Field(..., description="用户问题")
    images: Optional[List[str]] = Field(default=None, description="Base64 编码的图片列表")
    use_rag: bool = Field(default=True, description="是否启用 RAG 检索")
    conversation_history: Optional[List[ChatMessage]] = Field(default=None, description="对话历史")


class SourceDoc(BaseModel):
    content: str
    source: str
    score: float


class ChatResponse(BaseModel):
    answer: str
    sources: Optional[List[SourceDoc]] = None
    has_images_in_answer: bool = False
