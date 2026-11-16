"""Kafka 消息契约（所有 topic 的 payload 都在这里定义，生产/消费两端共用）。

设计要点
--------
* payload 一律是 **JSON dict**，小体积、可读、便于 kafka-topics --from-beginning 直接看；
* 大体积内容（解析出的 blocks/images）不进消息体，落盘后用 artifact 路径传递
  —— 这是 Kafka 的常规做法，避免超过 broker 的 message.max.bytes；
* 每条消息都带 ``doc_id`` / ``request_id`` 作为 key，保证**同一文档/同一请求**
  的消息落到同一分区、被同一消费者串行处理（顺序性）。

状态机（功能 2）
----------------
    pending(待解析) --Worker 取到任务--> parsing(解析中) --入库完成--> done(解析完成)
                                          └--任一步异常--> failed(解析失败)

parsing 内部再分细粒度 stage，前端显示「MinerU 解析中 / 抽取图片 / 向量化中 / 生成图片描述」
并配进度条，最终仍然只对应「解析中」这个大状态。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 状态与阶段
# ---------------------------------------------------------------------------
STATE_PENDING = "pending"
STATE_PARSING = "parsing"
STATE_DONE = "done"
STATE_FAILED = "failed"

STATE_LABELS = {
    STATE_PENDING: "待解析",
    STATE_PARSING: "解析中",
    STATE_DONE: "解析完成",
    STATE_FAILED: "解析失败",
}

STAGE_LABELS = {
    "queued": "排队中",
    "mineru": "MinerU 解析中",
    "mineru_fallback": "内置解析器解析中",
    "images": "抽取图片中",
    "chunking": "切分文本中",
    "embedding": "向量化中",
    "captioning": "生成图片描述中",
    "indexing": "写入索引中",
    "indexed": "索引完成",
    "retrieving": "检索中",
    "llm": "大模型生成中",
    "finished": "完成",
    "error": "出错",
}

# 各 stage 在「解析中」区间内的进度百分比
STAGE_PROGRESS = {
    "queued": 2,
    "mineru": 10,
    "mineru_fallback": 10,
    "images": 40,
    "chunking": 50,
    "embedding": 70,
    "captioning": 82,
    "indexing": 92,
    "indexed": 100,
    "error": 100,
}


def new_id(prefix: str = "") -> str:
    """生成带前缀的短 id，例如 ``doc_7f3ab2c1``。"""
    tail = uuid.uuid4().hex[:12]
    return f"{prefix}{tail}" if prefix else tail


# ---------------------------------------------------------------------------
# 1) Web -> 解析 Worker：待解析任务
# ---------------------------------------------------------------------------
def upload_msg(
    doc_id: str,
    filename: str,
    pdf_path: str,
    size_bytes: int,
    sha256: str,
    pages: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "type": "upload",
        "doc_id": doc_id,
        "filename": filename,
        "pdf_path": pdf_path,
        "size_bytes": size_bytes,
        "sha256": sha256,
        "pages": pages,
        "ts": time.time(),
    }


# ---------------------------------------------------------------------------
# 2) 各 Worker -> Web：解析状态（功能 2 的数据源）
# ---------------------------------------------------------------------------
def status_msg(
    doc_id: str,
    state: str,
    stage: str = "",
    progress: Optional[float] = None,
    message: str = "",
    filename: str = "",
    **extra: Any,
) -> Dict[str, Any]:
    """state ∈ {pending, parsing, done, failed}；progress ∈ [0,100]，None 表示不改变。"""
    if progress is None:
        progress = 100.0 if state == STATE_DONE else STAGE_PROGRESS.get(stage, 0.0)
    if state == STATE_DONE:
        progress = 100.0
    body = {
        "type": "status",
        "doc_id": doc_id,
        "filename": filename,
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "stage": stage,
        "stage_label": STAGE_LABELS.get(stage, stage),
        "progress": round(float(progress), 1),
        "message": message,
        "ts": time.time(),
    }
    body.update({k: v for k, v in extra.items() if v is not None})
    return body


def upload_accepted_msg(doc_id: str, filename: str, topic: str) -> Dict[str, Any]:
    """Web 进程在投递成功后立刻发一条「待解析」，让页面秒级出现该行。"""
    return status_msg(
        doc_id,
        STATE_PENDING,
        stage="queued",
        message=f"已投递到 Kafka topic「{topic}」，等待解析 Worker",
        filename=filename,
    )


# ---------------------------------------------------------------------------
# 3) 解析 Worker -> 入库 Worker：解析产物交接
# ---------------------------------------------------------------------------
def parsed_msg(
    doc_id: str,
    filename: str,
    artifact_dir: str,
    parser: str,
    pages: int,
    n_text_blocks: int,
    n_images: int,
) -> Dict[str, Any]:
    return {
        "type": "parsed",
        "doc_id": doc_id,
        "filename": filename,
        "artifact_dir": artifact_dir,   # 内含 blocks.json / full.md / images/
        "parser": parser,               # mineru | pdf_local
        "pages": pages,
        "n_text_blocks": n_text_blocks,
        "n_images": n_images,
        "ts": time.time(),
    }


# ---------------------------------------------------------------------------
# 4) Web -> QA Worker：多模态提问
# ---------------------------------------------------------------------------
def qa_request_msg(
    request_id: str,
    session_id: str,
    question: str,
    images: List[Dict[str, Any]],
    doc_ids: Optional[List[str]] = None,
    top_k: Optional[int] = None,
) -> Dict[str, Any]:
    """images: [{"image_id": int, "path": str, "mime": str, "name": str}] —— 已落盘，只传路径。"""
    return {
        "type": "qa_request",
        "request_id": request_id,
        "session_id": session_id,
        "question": question,
        "images": images,
        "doc_ids": doc_ids or [],
        "top_k": top_k,
        "ts": time.time(),
    }


# ---------------------------------------------------------------------------
# 5) QA Worker -> Web：答案流（增量 / 引用 / 结束 / 错误）
# ---------------------------------------------------------------------------
def qa_answer_msg(request_id: str, kind: str, **payload: Any) -> Dict[str, Any]:
    """kind: stage | delta | answer | error"""
    body = {
        "type": "qa_answer",
        "request_id": request_id,
        "kind": kind,
        "ts": time.time(),
    }
    body.update(payload)
    return body


def qa_stage_msg(request_id: str, stage: str, message: str = "", **extra: Any) -> Dict[str, Any]:
    return qa_answer_msg(
        request_id, "stage", stage=stage, stage_label=STAGE_LABELS.get(stage, stage),
        message=message, **extra
    )


def qa_delta_msg(request_id: str, delta: str) -> Dict[str, Any]:
    return qa_answer_msg(request_id, "delta", delta=delta)


def qa_final_msg(
    request_id: str,
    answer: str,
    citations: List[Dict[str, Any]],
    used_images: List[Dict[str, Any]],
    model: str,
    mock: bool,
    stats: Dict[str, Any],
) -> Dict[str, Any]:
    return qa_answer_msg(
        request_id, "answer", answer=answer, citations=citations, used_images=used_images,
        model=model, mock=mock, stats=stats,
    )


def qa_error_msg(request_id: str, error: str) -> Dict[str, Any]:
    return qa_answer_msg(request_id, "error", error=error)


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def check(msg: Dict[str, Any], *fields: str) -> bool:
    return isinstance(msg, dict) and all(msg.get(f) is not None for f in fields)
