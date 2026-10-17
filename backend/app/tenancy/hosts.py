"""Epic 38, US38.1: which tenant a host name reaches.

`tenants` and `tenant_domains` are platform tables, without row-level
security, so they're readable before a tenant is known. The app role may
only read them; `python -m app.tenancy` (as the tables' owner) writes them.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TenantDomainModel, TenantModel

from .context import Tenant


def normalize_host(host: str) -> str:
    """`Meridian.Localhost:3000` -> `meridian.localhost`; `[::1]:8000` -> `::1`."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:].split("]", 1)[0]
    if host.count(":") == 1:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def to_tenant(row: TenantModel) -> Tenant:
    return Tenant(id=row.id, slug=row.slug, display_name=row.display_name, status=row.status, region=row.region)


def tenant_for_host(session: Session, host: str) -> Tenant | None:
    row = session.scalar(
        select(TenantModel)
        .join(TenantDomainModel, TenantDomainModel.tenant_id == TenantModel.id)
        .where(TenantDomainModel.host == normalize_host(host))
    )
    return to_tenant(row) if row is not None else None


def resolve_host(host: str) -> Tenant | None:
    from app.db.session import get_session_factory

    with get_session_factory()() as session:
        return tenant_for_host(session, host)


def tenant_ids(session: Session) -> list[str]:
    """Every tenant, suspended ones too: for work that only protects them,
    like ending sessions at the IdP's request (US38.7)."""
    return list(session.scalars(select(TenantModel.id).where(TenantModel.status != "deleting").order_by(TenantModel.id)))


def active_tenant_ids(session: Session) -> list[str]:
    """For work that runs across tenants, one tenant at a time (the job
    worker). Ordered so every worker walks them the same way."""
    return list(session.scalars(select(TenantModel.id).where(TenantModel.status == "active").order_by(TenantModel.id)))
