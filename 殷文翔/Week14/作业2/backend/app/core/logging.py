"""JSON console logging. Bodies, credentials and SQL parameters are never logged."""
import json
import logging
import re
from contextvars import ContextVar
from datetime import datetime, timezone

from app.core.config import Settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: list[str]):
        super().__init__()
        self.secrets = sorted((s for s in secrets if s), key=len, reverse=True)

    def redact(self, value: str) -> str:
        for secret in self.secrets:
            value = value.replace(secret, "[REDACTED]")
        value = re.sub(r"(?i)([a-z][a-z0-9+.-]*://[^\s/:]+:)[^@\s]+@", r"\1[REDACTED]@", value)
        value = re.sub(r"(?i)(bearer\s+)\S+", r"\1[REDACTED]", value)
        return re.sub(r"(?i)((?:password|token|api_key|authorization)[\"']?\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", value)

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(), "level": record.levelname,
            "logger": record.name, "request_id": request_id_var.get(),
            "message": self.redact(record.getMessage()),
        }
        if record.exc_info:
            # Exception text can contain submitted values/SQL. Keep only the class name.
            payload["exception_type"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(settings: Settings) -> None:
    secrets = [getattr(settings, name).get_secret_value() for name in (
        "mysql_password", "redis_password", "es_password", "llm_api_key",
        "embedding_api_key", "rerank_api_key",
    )]
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter(secrets))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy.engine"):
        logger = logging.getLogger(name)
        logger.handlers = []
        logger.propagate = True
    # Our middleware emits sanitized access events without raw paths/query strings.
    logging.getLogger("uvicorn.access").disabled = True
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
