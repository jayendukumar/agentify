"""Epic 38, US38.13: suspending, deleting and erasing a tenant.

    active --suspend--> suspended --delete--> deleting --(wait)--> erased
           <-activate--           <-restore--

- **Suspended:** the tenant's hosts answer 403 (sign-ins, the API and SCIM
  alike, app/tenancy/middleware.py) and the job worker skips it, so its
  jobs wait. Nothing is removed.
- **Deleting:** still unreachable, and its data may be erased once
  TENANT_DELETION_WAIT_DAYS have passed since the operator deleted it.
  Until then an operator can restore it (back to suspended).
- **Erased:** every tenant row is deleted -- one statement per table, as
  the tenant, under row-level security, children first -- then its files
  and its secrets. The console keeps a deletion certificate (counts per
  table, times) in the platform audit log and removes the tenant's own
  `tenants` and `tenant_domains` rows, so the slug and hosts are free again.

The tenant has no encryption key of its own yet (object storage, Critical
Review weakness 4, brings one): the certificate says so rather than
claiming one was destroyed.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select

from app import secret_store
from app.config import get_settings
from app.db.models import TenantExportModel, TenantModel
from app.db.session import get_session_factory
from app.ids import utcnow

from . import data, databases
from .context import tenant_scope
from .files import tenant_dir
from .limits import forget_cached_limits


class LifecycleError(ValueError):
    pass


def now() -> datetime:
    return utcnow().replace(tzinfo=None)


# action: (from, to)
TRANSITIONS = {
    "suspend": ({"active"}, "suspended"),
    "activate": ({"suspended"}, "active"),
    "delete": ({"suspended"}, "deleting"),
    "restore": ({"deleting"}, "suspended"),
}


def change_status(tenant: TenantModel, action: str) -> str:
    """Returns the status before."""
    allowed, to = TRANSITIONS[action]
    if tenant.status not in allowed:
        if action == "delete":
            raise LifecycleError("Suspend the tenant first")
        raise LifecycleError(f"A {tenant.status} tenant can't be {'restored' if action == 'restore' else to}")
    before, tenant.status = tenant.status, to
    if action == "suspend":
        tenant.suspended_at = now()
    elif action == "activate":
        tenant.suspended_at = None
    elif action == "delete":
        tenant.delete_after = now() + timedelta(days=get_settings().tenant_deletion_wait_days)
    elif action == "restore":
        tenant.delete_after = None
    forget_cached_limits()
    return before


def check_erasable(tenant: TenantModel) -> None:
    if tenant.status != "deleting":
        raise LifecycleError("Only a deleted tenant can be erased: suspend it, then delete it")
    if tenant.delete_after is None or tenant.delete_after > now():
        raise LifecycleError(f"The waiting period runs until {tenant.delete_after:%Y-%m-%d %H:%M} UTC"
                             if tenant.delete_after else "The waiting period hasn't started")


@dataclass
class LastExport:
    status: str
    created_at: datetime
    downloaded_at: datetime | None


def last_export(tenant_id: str) -> LastExport | None:
    """For the console: whether the admin took their data. As the tenant."""
    with tenant_scope(tenant_id), get_session_factory()() as db:
        row = db.scalar(select(TenantExportModel).order_by(TenantExportModel.created_at.desc()).limit(1))
        return LastExport(row.status, row.created_at, row.downloaded_at) if row is not None else None


@dataclass
class Erasure:
    tables: dict[str, int]
    files_removed: int
    bytes_removed: int
    secrets: dict
    started_at: datetime
    finished_at: datetime = field(default_factory=now)

    @property
    def rows(self) -> int:
        return sum(self.tables.values())


def _erase_rows(tenant_id: str) -> dict[str, int]:
    """One DELETE per tenant table, children first, in one transaction, as
    the tenant: row-level security limits every statement to its rows.
    Then every table is counted again and must be empty."""
    tables = data.tenant_tables()
    with tenant_scope(tenant_id), get_session_factory()() as db, db.begin():
        counts = {table.name: db.execute(table.delete()).rowcount for table in reversed(tables)}
        left = {t.name: n for t in tables if (n := db.scalar(select(func.count()).select_from(t)))}
        if left:
            raise LifecycleError(f"Rows remained after erasing: {left}")
    return dict(sorted(counts.items()))


def _erase_files(tenant_id: str) -> tuple[int, int]:
    root = tenant_dir(get_settings(), tenant_id)
    if not root.exists():
        return 0, 0
    files = [p for p in root.rglob("*") if p.is_file()]
    size = sum(p.stat().st_size for p in files)
    shutil.rmtree(root)
    if root.exists():
        raise LifecycleError(f"Couldn't remove {root}")
    return len(files), size


def erase(tenant: TenantModel, *, other_slugs: list[str]) -> Erasure:
    """Erases the tenant's rows, files and secrets. Safe to run again after
    a failure part way: what's already gone counts as zero."""
    check_erasable(tenant)
    started = now()
    tables = _erase_rows(tenant.id)
    files, size = _erase_files(tenant.id)
    secrets = secret_store.erase(tenant.slug, other_slugs=other_slugs)
    forget_cached_limits()
    databases.forget()
    return Erasure(tables=tables, files_removed=files, bytes_removed=size, secrets=secrets, started_at=started)


def certificate(tenant: TenantModel, erasure: Erasure, *, operator_name: str) -> dict:
    """What the platform log keeps: which tenant, when, and how much of
    each kind was removed. Counts and table names only -- no content."""
    return {
        "certificate": "tenant-deletion",
        "tenant_id": tenant.id,
        "slug": tenant.slug,
        "region": tenant.region,
        "suspended_at": tenant.suspended_at.isoformat() if tenant.suspended_at else None,
        "deletion_requested_wait_until": tenant.delete_after.isoformat() if tenant.delete_after else None,
        "erased_at": erasure.finished_at.isoformat(),
        "started_at": erasure.started_at.isoformat(),
        "erased_by": operator_name,
        "tables": erasure.tables,
        "rows_total": erasure.rows,
        "files_removed": erasure.files_removed,
        "bytes_removed": erasure.bytes_removed,
        "secrets": erasure.secrets,
        "encryption_key": "none: tenant data isn't encrypted under a key of its own yet (weakness 4)",
        "database": ("dedicated: its rows are erased; drop the database itself" if tenant.database_ref
                     else "shared"),
        "verified_empty": True,
    }
