"""Epic 38, US38.9: what each tenant may use -- concurrent jobs, LLM
requests per minute, and a monthly LLM budget.

An operator sets them on the tenant (a platform table, so the tenant can
read them but not raise them); a limit left empty takes the deployment's
default from the `TENANT_*` settings, and no default means no limit.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import TenantModel

# The budget warns at this share of the month's budget, and stops at all of it.
BUDGET_WARN_RATIO = 0.8


@dataclass(frozen=True)
class TenantLimits:
    tenant_id: str
    max_concurrent_jobs: int | None
    llm_requests_per_minute: int | None
    llm_monthly_budget_usd: float | None


def limits_of(tenant: TenantModel) -> TenantLimits:
    settings = get_settings()

    def pick(own, default):
        return own if own is not None else default

    return TenantLimits(
        tenant_id=tenant.id,
        max_concurrent_jobs=pick(tenant.max_concurrent_jobs, settings.tenant_max_concurrent_jobs),
        llm_requests_per_minute=pick(tenant.llm_requests_per_minute, settings.tenant_llm_requests_per_minute),
        llm_monthly_budget_usd=pick(tenant.llm_monthly_budget_usd, settings.tenant_llm_monthly_budget_usd),
    )


def active_tenant_limits(session: Session) -> list[TenantLimits]:
    """The active tenants and their limits, in the order every worker walks
    them (as `active_tenant_ids`)."""
    rows = session.scalars(select(TenantModel).where(TenantModel.status == "active").order_by(TenantModel.id))
    return [limits_of(row) for row in rows]


_CACHE_SECONDS = 30.0
_cache: dict[str, tuple[float, TenantLimits]] = {}


def limits_for(tenant_id: str) -> TenantLimits:
    """For every LLM call, so read at most every 30 seconds per tenant: an
    operator's change takes effect within that."""
    cached = _cache.get(tenant_id)
    if cached is not None and time.monotonic() - cached[0] < _CACHE_SECONDS:
        return cached[1]
    from app.db.session import get_session_factory

    with get_session_factory()() as session:
        row = session.get(TenantModel, tenant_id)
        limits = limits_of(row) if row is not None else TenantLimits(tenant_id, None, None, None)
    _cache[tenant_id] = (time.monotonic(), limits)
    return limits


def forget_cached_limits() -> None:
    _cache.clear()
