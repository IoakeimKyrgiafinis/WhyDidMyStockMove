"""Request IDs, one structured log line per request, and HTTP cache headers.

Written as plain ASGI middleware (not BaseHTTPMiddleware) so Server-Sent Events streams are
passed through untouched instead of being buffered.
"""

from __future__ import annotations

import contextvars
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from datetime import UTC, datetime
from typing import Any

from whymoved.memory import release_memory_every

Scope = MutableMapping[str, Any]
Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]
ASGIApp = Callable[[Scope, Receive, Send], Awaitable[None]]

request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")
access_log = logging.getLogger("whymoved.access")

RELEASE_EVERY_SECONDS = 10.0
_VALID_ID = re.compile(r"[A-Za-z0-9._-]{8,64}")
# The data changes once a night, so browsers and CDNs may reuse answers for a while.
DATA_MAX_AGE = 300
META_MAX_AGE = 30


class JsonFormatter(logging.Formatter):
    """One JSON object per line: easy to search in a hosting provider's log viewer."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id.get(),
        }
        extra = getattr(record, "fields", None)
        if isinstance(extra, dict):
            entry.update(extra)
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    for name in ("uvicorn.access",):  # replaced by our own access line
        logging.getLogger(name).disabled = True


def _cache_control(path: str, status: int, content_type: str) -> str | None:
    if not path.startswith("/api/v1/"):
        return None
    if status != 200 or content_type.startswith("text/event-stream"):
        return "no-store"
    if path == "/api/v1/meta":
        return f"public, max-age={META_MAX_AGE}"
    return f"public, max-age={DATA_MAX_AGE}"


class RequestContextMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope.get("headers") or []).get(b"x-request-id", b"").decode("latin-1")
        rid = incoming if _VALID_ID.fullmatch(incoming) else uuid.uuid4().hex[:16]
        token = request_id.set(rid)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                headers = list(message.get("headers") or [])
                names = {k.lower() for k, _ in headers}
                content_type = next(
                    (v.decode("latin-1") for k, v in headers if k.lower() == b"content-type"), ""
                )
                headers.append((b"x-request-id", rid.encode()))
                cache = _cache_control(scope["path"], status, content_type)
                if cache and b"cache-control" not in names:
                    headers.append((b"cache-control", cache.encode()))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            access_log.info(
                "request",
                extra={
                    "fields": {
                        "method": scope.get("method"),
                        "path": scope.get("path"),
                        "status": status,
                        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    }
                },
            )
            request_id.reset(token)
            # Heavy requests (a peer grid, a replay) free large temporaries; hand them back.
            release_memory_every(RELEASE_EVERY_SECONDS)
