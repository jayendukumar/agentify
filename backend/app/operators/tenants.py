"""Epic 38, US38.8: what the operator console does with tenants.

The platform session (`platform_db`) only ever touches platform tables.
Anything about a tenant's own rows -- the usage counts, its support grants,
the one-time support entry -- runs as that tenant, through the app role
and its row-level security, in a short session of its own."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import support
from app.config import get_settings
from app.db.models import (
    JobModel,
    LlmUsageMonthlyModel,
    ProcessModel,
    SessionModel,
    SupportGrantModel,
    TenantDomainModel,
    TenantModel,
    UserModel,
)
from app.db.session import get_session_factory
from app.ids import new_id
from app.llm import metering
from app.tenancy import tenant_scope
from app.tenancy.hosts import normalize_host
from app.tenancy.identity import with_host
from app.tenancy.limits import forget_cached_limits

SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


class TenantError(ValueError):
    pass


@dataclass
class Usage:
    users: int
    processes: int
    jobs_waiting: int
    last_sign_in_at: datetime | None
    support_grants_active: int
    llm_calls_this_month: int
    llm_cost_this_month_usd: float


def usage(tenant_id: str) -> Usage:
    """Counts and costs only (decided 2026-10-15: platform analytics keep
    counts and costs, never content). As the tenant, under row-level
    security."""
    with tenant_scope(tenant_id), get_session_factory()() as db:
        count = lambda query: db.scalar(select(func.count()).select_from(query.subquery()))  # noqa: E731
        return Usage(
            users=count(select(UserModel.id).where(UserModel.active, UserModel.actor_type == "user")),
            processes=count(select(ProcessModel.id)),
            jobs_waiting=count(select(JobModel.id).where(JobModel.status.in_(("queued", "running")))),
            last_sign_in_at=db.scalar(
                select(func.max(SessionModel.created_at)).where(SessionModel.support_grant_id.is_(None))
            ),
            support_grants_active=len(support.active_grants(db)),
            llm_calls_this_month=db.scalar(
                select(func.coalesce(func.sum(LlmUsageMonthlyModel.calls), 0))
                .where(LlmUsageMonthlyModel.month == metering.month_of(metering.utcnow()))
            ),
            llm_cost_this_month_usd=round(metering.month_spend(db), 4),
        )


def hosts(platform_db: Session, tenant_id: str) -> list[str]:
    return sorted(platform_db.scalars(select(TenantDomainModel.host).where(TenantDomainModel.tenant_id == tenant_id)))


def create(platform_db: Session, *, slug: str, display_name: str, host: str, region: str | None) -> TenantModel:
    slug = slug.strip().lower()
    if not SLUG.match(slug):
        raise TenantError("The slug is 2-40 lowercase letters, digits and hyphens, starting with a letter")
    host = normalize_host(host)
    if not host or host in get_settings().operator_host_set:
        raise TenantError("Give the tenant a host name of its own")
    if platform_db.scalar(select(TenantModel).where(TenantModel.slug == slug)) is not None:
        raise TenantError(f"A tenant '{slug}' already exists")
    if platform_db.get(TenantDomainModel, host) is not None:
        raise TenantError(f"Host '{host}' already reaches a tenant")
    tenant = TenantModel(id=new_id("ten"), slug=slug, display_name=display_name.strip() or slug.title(),
                         region=(region or "").strip() or None)
    platform_db.add(tenant)
    platform_db.flush()
    platform_db.add(TenantDomainModel(host=host, tenant_id=tenant.id))
    platform_db.flush()
    return tenant


LIMIT_FIELDS = ("max_concurrent_jobs", "llm_requests_per_minute", "llm_monthly_budget_usd")


def set_limits(tenant: TenantModel, values: dict) -> dict:
    """US38.9: the tenant's own limits; None falls back to the deployment's
    default. Returns what they were."""
    before = {name: getattr(tenant, name) for name in LIMIT_FIELDS}
    for name in LIMIT_FIELDS:
        setattr(tenant, name, values.get(name))
    forget_cached_limits()
    return before


TRANSITIONS = {
    "suspend": ({"active"}, "suspended"),
    "activate": ({"suspended"}, "active"),
    # Deleting the data (export first, a waiting period, a certificate) is
    # US38.13; until then a deleted tenant is unreachable and kept.
    "delete": ({"suspended"}, "deleting"),
}


def change_status(platform_db: Session, tenant: TenantModel, action: str) -> str:
    allowed, to = TRANSITIONS[action]
    if tenant.status not in allowed:
        raise TenantError(
            "Suspend the tenant first" if action == "delete" else f"A {tenant.status} tenant can't be {to}"
        )
    before, tenant.status = tenant.status, to
    return before


# ---- support ------------------------------------------------------------------------


def grants_for(tenant_id: str, operator_email: str | None) -> list[SupportGrantModel]:
    """The tenant's live grants this operator may use."""
    with tenant_scope(tenant_id), get_session_factory()() as db:
        return [g for g in support.active_grants(db) if support.allows(g, operator_email)]


def entry_url(tenant_hosts: list[str], token: str) -> str:
    """On the tenant's own host -- preferring one under the same parent
    domain as the console (`ops.localhost` -> `northwind.localhost`), so
    the browser's cookie lands where the tenant's frontend calls."""
    settings = get_settings()
    suffixes = {"." + h.split(".", 1)[1] for h in settings.operator_host_set if "." in h}
    preferred = [h for h in tenant_hosts if any(h.endswith(s) for s in suffixes)]
    host = (preferred or tenant_hosts)[0]
    return with_host(settings.api_public_url, host) + f"/api/auth/support/enter?token={token}"


def open_support(tenant_id: str, grant_id: str, *, operator_id: str, operator_name: str,
                 operator_email: str | None) -> tuple[SupportGrantModel, str]:
    """Checks the grant as the tenant and writes the one-time entry."""
    with tenant_scope(tenant_id), get_session_factory()() as db, db.begin():
        grant = db.get(SupportGrantModel, grant_id)
        if not support.is_alive(grant) or not support.allows(grant, operator_email):
            raise TenantError("No live support grant for you in this tenant")
        token = support.create_entry(db, grant, operator_id=operator_id, operator_name=operator_name,
                                     operator_email=operator_email)
        return grant, token
