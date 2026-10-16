"""Epic 38, US38.11: whose key pays for a call to the default model
provider -- the platform's (`OPENROUTER_API_KEY`), or the tenant's own.

A tenant's admin sets a reference to its own key on the organization
(`profile.llm_provider_key_ref`, `secret://tenant/<name>`). Once set, its
calls to the default provider use that key, and if the secret is missing
they're refused, never quietly put on the platform's key. Calls to a
self-hosted endpoint (the model policy's `base_url`) are on the tenant's
side too. Either way the usage is the tenant's (app/llm/metering.py).
"""

from __future__ import annotations

import time
from typing import Literal

from sqlalchemy import select

from app.tenancy.context import current_tenant_id

KeySource = Literal["platform", "tenant"]
PROFILE_KEY = "llm_provider_key_ref"

_CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, str | None]] = {}


def tenant_key_ref() -> str | None:
    """The current tenant's own provider key reference, if it set one. Read
    at most every 30 seconds per tenant (at once in the process that saved
    it)."""
    tenant_id = current_tenant_id()
    if tenant_id is None:
        return None
    cached = _cache.get(tenant_id)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    from app.db.models import OrganizationModel
    from app.db.session import get_session_factory

    with get_session_factory()() as session:
        profile = session.scalar(select(OrganizationModel.profile).limit(1)) or {}
    ref = profile.get(PROFILE_KEY) or None
    _cache[tenant_id] = (time.monotonic(), ref)
    return ref


def forget() -> None:
    _cache.clear()
