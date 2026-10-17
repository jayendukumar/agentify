"""Epic 38, US38.3: which tenant the current work belongs to.

Set once at each edge -- the tenant middleware for a request, the job
worker for each job, `tenant_scope()` in scripts and tests -- and read by
app/db/session.py, which hands it to Postgres at the start of every
transaction (`SET LOCAL app.tenant_id`). Code in between never passes it
around and never filters on it: row-level security does. Code that runs
without one sees no rows and can't insert, so a missing edge fails closed.

A ContextVar rather than an argument because it has to reach sessions
opened deep inside request handling (the audit log's refusal writer, the
local registry, background tasks), and contextvars follow the request
into thread pools and the tasks it starts.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Tenant:
    id: str
    slug: str
    display_name: str
    status: str
    region: str | None = None

    @property
    def active(self) -> bool:
        return self.status == "active"


_current_tenant_id: ContextVar[str | None] = ContextVar("tenant_id", default=None)
# US38.6: the host the request came in on (without the port), which a
# tenant's sign-in and SCIM URLs are derived from.
_current_host: ContextVar[str | None] = ContextVar("tenant_host", default=None)


def current_tenant_id() -> str | None:
    return _current_tenant_id.get()


def current_host() -> str | None:
    return _current_host.get()


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[None]:
    """Runs the block as `tenant_id`: every session transaction opened
    inside it sees only that tenant's rows."""
    token = _current_tenant_id.set(tenant_id)
    try:
        yield
    finally:
        _current_tenant_id.reset(token)


def set_current_tenant(tenant_id: str | None, host: str | None = None):
    """For edges that can't use a `with` block (the tenant middleware, test
    fixtures). Returns the token to reset with."""
    return _current_tenant_id.set(tenant_id), _current_host.set(host)


def reset_current_tenant(token) -> None:
    tenant_token, host_token = token
    _current_tenant_id.reset(tenant_token)
    _current_host.reset(host_token)
