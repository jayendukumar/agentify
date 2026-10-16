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
    outside a request, e.g. at startup or in a background task).

    Epic 38, US38.10: and with the tenant it ran as (or "-" outside any:
    startup, the worker between tenants, the operator console), so one
    tenant's lines can be pulled out of a shared log."""

    def filter(self, record: logging.LogRecord) -> bool:
        from app.tenancy.context import current_tenant_id

        record.request_id = _request_id.get()
        if not getattr(record, "tenant_id", None):  # an explicit extra={"tenant_id": ...} wins
            record.tenant_id = current_tenant_id() or "-"
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


def configure_logging() -> None:
    """Epic 10: console output alone (the previous logging.basicConfig)
    doesn't survive past the terminal it ran in -- app_log_enabled adds a
    rotating-free (dev-scale, see the app_log_path setting) file handler
    alongside it, so request/LLM-call/error logs persist across restarts
    for later review, not just the current session's console scrollback.
    US10.1: every handler also gets RequestIDLogFilter so %(request_id)s
    is always populated, in or out of a request. Used by the API and by a
    standalone job worker (Epic 38, US38.10), so both log the tenant.
    """
    from app.config import get_settings

    settings = get_settings()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if settings.app_log_enabled:
        settings.app_log_file_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(settings.app_log_file_path, encoding="utf-8"))
    request_id_filter = RequestIDLogFilter()
    for handler in handlers:
        handler.addFilter(request_id_filter)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(request_id)s] [tenant=%(tenant_id)s] %(name)s: %(message)s",
        handlers=handlers,
        force=True,  # re-configure cleanly even if something already called basicConfig
    )
