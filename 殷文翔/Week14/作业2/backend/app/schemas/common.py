from datetime import datetime, timezone
from typing import Annotated, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def utc_iso(value: datetime) -> str:
    return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)).isoformat().replace("+00:00", "Z")


UTCTimestamp = Annotated[datetime, PlainSerializer(utc_iso, return_type=str, when_used="json")]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class ErrorDetail(Contract):
    code: str
    message: str
    retryable: bool = False
    details: dict = Field(default_factory=dict)


class ErrorResponse(Contract):
    error: ErrorDetail
    request_id: str


T = TypeVar("T")


class Page(Contract, Generic[T]):
    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class Pagination(Contract):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
