"""Epic 10, US10.1: a per-request correlation id threaded through every
log line for the duration of that HTTP request -- lets you grep one
request's full log trail out of a busy log file. Scoped honestly: this
correlates logs within one HTTP request/response cycle only.
app/ingestion/pipeline.py's background ingestion task runs detached from
that request context by design (BackgroundTasks execute after the
response, and this project's ingestion is deliberately fire-and-forget --
see that module's docstring) and already correlates via process_id/
document_id in its own log calls; forcing the original request's id across
that async boundary would be over-engineering for a local, single-process
app with no distributed tracing infrastructure to feed it into.
"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar
from typing import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

_request_id: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIDLogFilter(logging.Filter):
    """Stamps every log record with the current request id (or "-"
    outside a request, e.g. at startup or in a background task)."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id.get()
        return True


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    # Reuse a caller-supplied id (e.g. from an upstream proxy) if present,
    # so a request can be traced across process boundaries too -- fall
    # back to minting a fresh one, same length as this project's other
    # short ids (app/ids.py's new_id) for consistency in log output.
    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:12]
    token = _request_id.set(request_id)
    try:
        response = await call_next(request)
    finally:
        _request_id.reset(token)
    response.headers[REQUEST_ID_HEADER] = request_id
    return response
