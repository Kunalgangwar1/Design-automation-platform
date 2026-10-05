"""HTTP correlation-ID and request-latency middleware."""

from __future__ import annotations

import logging
import re
from time import perf_counter
from uuid import uuid4

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from design_automation.core.observability import (
    elapsed_ms,
    log_event,
    reset_request_id,
    set_request_id,
)


REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
logger = logging.getLogger(__name__)


def select_request_id(incoming: str | None) -> str:
    """Accept a small printable ID or replace it with an application UUID."""

    candidate = (incoming or "").strip()
    return candidate if _SAFE_REQUEST_ID.fullmatch(candidate) else str(uuid4())


class RequestContextMiddleware:
    """Attach an untrusted observability ID to context, logs, and responses."""

    def __init__(self, app: ASGIApp) -> None:
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        request_id = select_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        scope.setdefault("state", {})["request_id"] = request_id
        token = set_request_id(request_id)
        started = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
                headers = list(message.get("headers", []))
                header_name = REQUEST_ID_HEADER.lower().encode()
                if not any(name.lower() == header_name for name, _ in headers):
                    headers.append((header_name, request_id.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self._app(scope, receive, send_with_request_id)
        finally:
            log_event(
                logger,
                logging.INFO,
                "http_request_completed",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                latency_ms=elapsed_ms(started),
            )
            reset_request_id(token)
