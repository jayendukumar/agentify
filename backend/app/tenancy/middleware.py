"""Epic 38, US38.1/US38.3: every request runs as the tenant its host
reaches, decided before routing and before authentication.

- An unknown host gets a plain 404 that names no tenant.
- A suspended (or deleting) tenant gets a 403 saying so.
- An operator host (US38.8, OPERATOR_HOSTS) reaches no tenant: only the
  operator console's routes answer there, with no tenant set, so any
  tenant table reads as empty. Those routes answer nowhere else.
- Otherwise the tenant goes in `request.state.tenant` and the tenant
  context (app/tenancy/context.py) for the rest of the request, including
  its background tasks.

A pure ASGI middleware, not `@app.middleware("http")`, so the context it
sets is the one the endpoint, its thread pool and its background tasks
inherit.
"""

from __future__ import annotations

from starlette.concurrency import run_in_threadpool
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import get_settings

from . import residency
from .context import reset_current_tenant, set_current_tenant
from .hosts import normalize_host, resolve_host

# Paths that serve no tenant data: load balancers probe by IP address.
UNSCOPED_PATHS = frozenset({"/healthz"})
OPERATOR_PREFIX = "/api/operator"
HOST_KIND_PATH = "/api/host"


def _not_found() -> JSONResponse:
    return JSONResponse({"detail": "Not found"}, status_code=404)


class TenantMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or scope["path"] in UNSCOPED_PATHS:
            await self.app(scope, receive, send)
            return
        host = Headers(scope=scope).get("host", "")
        path = scope["path"]
        operator_path = path == OPERATOR_PREFIX or path.startswith(OPERATOR_PREFIX + "/")
        if normalize_host(host) in get_settings().operator_host_set:
            if operator_path or path == HOST_KIND_PATH:
                await self.app(scope, receive, send)
            else:
                await _not_found()(scope, receive, send)
            return
        if operator_path:
            await _not_found()(scope, receive, send)
            return
        tenant = await run_in_threadpool(resolve_host, host)
        if tenant is None:
            await _not_found()(scope, receive, send)
            return
        if not residency.serves(tenant.region):
            # US38.15: another region's tenant; its own deployment serves it.
            response = JSONResponse({"detail": "This organization is served from its own region."}, status_code=421)
            await response(scope, receive, send)
            return
        if not tenant.active:
            response = JSONResponse(
                {"detail": "This account is suspended. Contact your administrator."}, status_code=403
            )
            await response(scope, receive, send)
            return
        scope.setdefault("state", {})["tenant"] = tenant
        token = set_current_tenant(tenant.id, normalize_host(host))
        try:
            await self.app(scope, receive, send)
        finally:
            reset_current_tenant(token)
