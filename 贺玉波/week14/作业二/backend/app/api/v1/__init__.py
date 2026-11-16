"""业务路由聚合。P3 documents，P4 search，P5 chat，P6 traces。"""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1 import chat, documents, search, traces

router = APIRouter()
router.include_router(documents.router)
router.include_router(search.router)
router.include_router(chat.router)
router.include_router(traces.router)
