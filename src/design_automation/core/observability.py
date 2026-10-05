"""Small standard-library observability helpers for runtime boundaries."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from time import perf_counter
from typing import Any


_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the correlation ID associated with the current execution context."""

    return _request_id.get()


def set_request_id(value: str) -> Token[str | None]:
    return _request_id.set(value)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id.reset(token)


def elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))


class JsonLogFormatter(logging.Formatter):
    """Render one compact JSON object per log record."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": getattr(record, "event", record.getMessage()),
        }
        request_id = get_request_id()
        if request_id:
            payload["request_id"] = request_id
        payload.update(getattr(record, "fields", {}))
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    """Configure one simple JSON stream handler for the application process."""

    root = logging.getLogger()
    handler = next(
        (
            item
            for item in root.handlers
            if getattr(item, "_design_automation_handler", False)
        ),
        None,
    )
    if handler is None:
        handler = logging.StreamHandler()
        handler._design_automation_handler = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    handler.setFormatter(JsonLogFormatter())
    root.setLevel(level)


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    **fields: object,
) -> None:
    """Log a named event with deliberately selected, non-secret fields."""

    logger.log(level, event, extra={"event": event, "fields": fields})
