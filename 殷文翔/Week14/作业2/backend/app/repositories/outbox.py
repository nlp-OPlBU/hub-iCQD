"""Outbox inserts participate in the caller's transaction; never publish here."""
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import OutboxEvent
from app.schemas.document import ParseEvent


def add_parse_requested(session: Session, settings: Settings, event: ParseEvent) -> None:
    session.add(OutboxEvent(
        id=str(event.event_id), task_id=str(event.task_id), document_id=str(event.document_id),
        version=event.version, event_type="pdf.parse.requested", topic=settings.kafka_pdf_topic,
        payload=event.model_dump(mode="json"), status="pending", attempts=0,
        max_retries=settings.outbox_max_retries,
    ))
