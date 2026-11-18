import json
import logging
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


request_id_context: ContextVar[str] = ContextVar("request_id", default="-")


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_context.get(),
        }
        standard_fields = logging.makeLogRecord({}).__dict__
        extra_fields = {
            key: value
            for key, value in record.__dict__.items()
            if key not in standard_fields and key != "message"
        }
        if extra_fields:
            payload["context"] = extra_fields
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def new_request_id() -> str:
    return str(uuid4())
