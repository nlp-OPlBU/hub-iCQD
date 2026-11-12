import logging

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.errors import error_response
from app.schemas.common import Contract, ErrorResponse

router = APIRouter(tags=["health"])
SCHEMA_REVISION = "0001_initial"


class HealthResponse(Contract):
    status: str
    service: str
    scope: str


@router.get("/health", response_model=HealthResponse)
def health(request: Request):
    return HealthResponse(status="ok", service=request.app.state.settings.app_name, scope="process")


@router.get("/health/ready", response_model=HealthResponse, responses={503: {"model": ErrorResponse}})
def readiness(request: Request):
    try:
        with request.app.state.engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            revisions = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
            if revisions != [SCHEMA_REVISION]:
                return error_response(request, 503, "SCHEMA_NOT_READY", "数据库迁移版本不匹配", True)
    except SQLAlchemyError:
        logging.getLogger(__name__).warning("MySQL readiness check failed")
        return error_response(request, 503, "DATABASE_NOT_READY", "MySQL 不可用或尚未迁移", True)
    return HealthResponse(status="ok", service=request.app.state.settings.app_name, scope="mysql_schema")
