"""探针不调用模型，也不重新生成数据库目录。"""

from fastapi import APIRouter, Depends, Request

from ..dependencies import ensure_database_available, get_workflow
from ..schemas import ERROR_RESPONSES, LivenessResponse, ReadinessResponse


router = APIRouter(tags=["health"])


@router.get("/healthz", response_model=LivenessResponse, summary="服务存活检查")
async def liveness() -> LivenessResponse:
    return LivenessResponse()


@router.get(
    "/readyz", response_model=ReadinessResponse,
    dependencies=[Depends(get_workflow)],
    responses={503: ERROR_RESPONSES[503]}, summary="工作流和数据库就绪检查",
)
async def readiness(request: Request) -> ReadinessResponse:
    await ensure_database_available(request)
    return ReadinessResponse()
