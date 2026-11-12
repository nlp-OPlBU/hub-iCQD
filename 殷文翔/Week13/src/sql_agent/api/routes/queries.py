"""自然语言查询：验证 HTTP 输入后复用异步工作流。"""

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.routing import APIRoute

from ...models import WorkflowOutput
from ..dependencies import QueryWorkflow, ensure_database_available, get_workflow
from ..errors import APIError
from ..schemas import ERROR_RESPONSES, QueryRequest


logger = logging.getLogger(__name__)


class JSONRoute(APIRoute):
    """在 FastAPI 解析请求体之前检查媒体类型，保证错误类型返回 415。"""

    def get_route_handler(self) -> Callable[[Request], Awaitable[Response]]:
        handler = super().get_route_handler()

        async def require_json(request: Request) -> Response:
            media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            if media_type != "application/json" and not (
                media_type.startswith("application/") and media_type.endswith("+json")
            ):
                raise APIError(415, "unsupported_media_type", "请求必须使用 JSON 媒体类型。")
            return await handler(request)

        return require_json


router = APIRouter(prefix="/api/v1", tags=["queries"], route_class=JSONRoute)


@router.post(
    "/queries", response_model=WorkflowOutput, responses=ERROR_RESPONSES,
    summary="用自然语言查询数据库",
    description="等待单次工作流完成；澄清、空结果及汇总降级均为成功响应，无会话记忆。",
)
async def query(
    payload: QueryRequest,
    request: Request,
    workflow: Annotated[QueryWorkflow, Depends(get_workflow)],
) -> WorkflowOutput:
    await ensure_database_available(request)
    result = await workflow.run(payload.question)
    if not isinstance(result, WorkflowOutput):
        raise TypeError("工作流未返回 WorkflowOutput")
    if result.error:
        logger.error("request_id=%s workflow failed: %s", request.state.request_id, result.error)
        raise APIError(500, "workflow_failed", "查询处理失败，请调整问题或稍后重试。")
    return result
