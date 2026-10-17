"""Epic 38, US38.14: a tenant in a database of its own.

A tenant's `database_ref` names the app role's URL of its own database as
a secret reference (`secret://tenant/database_url`, so with the env backend
`TENANT_<SLUG>_DATABASE_URL`). Migrations use the secret `<name>_owner`
(`TENANT_<SLUG>_DATABASE_URL_OWNER`) when it's set, else the same URL.

- **Routing** (app/db/session.py, `TenantSession.get_bind`): a session
  working as a tenant with a dedicated database sends its statements
  there; statements that only touch platform tables (`tenants`,
  `tenant_domains`, the operator tables) stay on the shared database,
  which is the one that holds them. A tenant whose database secret is
  missing gets an error, never the shared database.
- **The same schema and the same rules:** migrations run against every
  tenant database (migrations/env.py), and row-level security stays on in
  them, as defence in depth. The tenant's own `tenants` row is copied in,
  for the foreign keys.
- **Moving in and out** (`python -m app.tenancy database attach|detach`):
  the tenant must be suspended. Every tenant table is copied, parents
  first, as the tenant, then sequences are moved past the copied ids, the
  reference is set (or cleared), and the rows are erased from where they
  were.

Which tenant has which database is read from the shared database at most
every CACHE_SECONDS per process; a move waits until a suspension is at
least that old, so no process still routes the tenant the old way.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from sqlalchemy import Connection, Engine, Table, create_engine, func, select, text
from sqlalchemy.orm import Session

from app import secret_store
from app.db.models import TenantModel

from . import data
from .context import tenant_scope

CACHE_SECONDS = 30.0
OWNER_SUFFIX = "_owner"


class DedicatedDatabaseError(RuntimeError):
    pass


_refs: dict[str, tuple[float, str | None, str | None]] = {}
_engines: dict[str, Engine] = {}


def forget() -> None:
    _refs.clear()


def _tenant_ref(tenant_id: str) -> tuple[str | None, str | None]:
    cached = _refs.get(tenant_id)
    if cached is not None and time.monotonic() - cached[0] < CACHE_SECONDS:
        return cached[1], cached[2]
    from app.db.session import get_engine

    with get_engine().connect() as connection:  # a platform table: readable without a tenant
        row = connection.execute(select(TenantModel.slug, TenantModel.database_ref)
                                 .where(TenantModel.id == tenant_id)).first()
    slug, ref = (row.slug, row.database_ref) if row is not None else (None, None)
    _refs[tenant_id] = (time.monotonic(), slug, ref)
    return slug, ref


def _engine(url: str) -> Engine:
    engine = _engines.get(url)
    if engine is None:
        engine = _engines[url] = create_engine(url, pool_pre_ping=True)
    return engine


def engine_for(tenant_id: str) -> Engine | None:
    """The tenant's own database, or None for the shared one."""
    slug, ref = _tenant_ref(tenant_id)
    if not ref:
        return None
    url = secret_store.resolve(ref, slug)
    if not url:
        raise DedicatedDatabaseError(
            f"Tenant {slug}'s database secret ({secret_store.where_to_put(ref, slug or '')}) isn't set")
    return _engine(url)


def owner_url(ref: str, slug: str) -> str:
    url = secret_store.resolve(ref + OWNER_SUFFIX, slug) or secret_store.resolve(ref, slug)
    if not url:
        raise DedicatedDatabaseError(f"Set {secret_store.where_to_put(ref, slug)} first")
    return url


def migrate(url: str) -> None:
    """Runs every migration against one database (as its owner)."""
    from alembic import command
    from alembic.config import Config

    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")


def tenant_databases(connection: Connection) -> list[tuple[str, str]]:
    """(slug, ref) of every tenant with a database of its own."""
    rows = connection.execute(text("SELECT slug, database_ref FROM tenants WHERE database_ref IS NOT NULL "
                                   "ORDER BY slug"))
    return [(r.slug, r.database_ref) for r in rows]


# ---- moving a tenant ------------------------------------------------------------------


def _as_tenant(connection: Connection, tenant_id: str) -> None:
    connection.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})


def _rows(connection: Connection, tenant_id: str) -> int:
    """The tenant's rows. Filtered by name too: an owner that's a superuser
    skips row-level security."""
    _as_tenant(connection, tenant_id)
    return sum(connection.scalar(select(func.count()).select_from(t).where(t.c.tenant_id == tenant_id))
               for t in data.tenant_tables())


def _sequences(table: Table) -> list[str]:
    return [c.name for c in table.columns
            if c.primary_key and c.type.python_type is int and (c.identity is not None or c.autoincrement is True)]


def _copy(source: Connection, target: Connection, tenant_id: str, report: Callable[[str], None]) -> dict[str, int]:
    """Every tenant table, parents first; both sides as the tenant."""
    _as_tenant(source, tenant_id)
    _as_tenant(target, tenant_id)
    counts: dict[str, int] = {}
    for table in data.tenant_tables():
        n = 0
        batch: list[dict] = []
        query = select(table).where(table.c.tenant_id == tenant_id).execution_options(yield_per=500)
        for row in source.execute(query).mappings():
            batch.append(dict(row))
            if len(batch) == 500:
                target.execute(table.insert(), batch)
                n, batch = n + len(batch), []
        if batch:
            target.execute(table.insert(), batch)
            n += len(batch)
        for column in _sequences(table):
            _advance(target, table.name, column)
        counts[table.name] = n
        if n:
            report(f"  {table.name}: {n}")
    return counts


def _advance(connection: Connection, table: str, column: str) -> None:
    """Moves the column's sequence past the copied ids -- only ever
    forward: in the shared database other tenants' ids may be further on."""
    sequence = connection.scalar(text("SELECT pg_get_serial_sequence(:t, :c)"), {"t": table, "c": column})
    if sequence is None:
        return
    current = connection.scalar(text(f"SELECT last_value FROM {sequence}"))
    highest = connection.scalar(text(f'SELECT MAX("{column}") FROM "{table}"'))
    if highest is not None and highest > current:
        connection.execute(text("SELECT setval(:s, :v)"), {"s": sequence, "v": highest})


def _prepare(target: Connection, tenant: TenantModel) -> None:
    """The tenant's own `tenants` row, for the foreign keys; the rows a
    fresh database's first migration made for a default tenant go, as do
    host names, which only the shared database resolves."""
    target.execute(text("DELETE FROM tenant_domains"))
    target.execute(text(
        "INSERT INTO tenants (id, slug, display_name, status, region, created_at, created_via) "
        "VALUES (:id, :slug, :name, :status, :region, :created_at, :via) "
        "ON CONFLICT (id) DO UPDATE SET slug = EXCLUDED.slug, display_name = EXCLUDED.display_name"),
        {"id": tenant.id, "slug": f"{tenant.slug}", "name": tenant.display_name, "status": tenant.status,
         "region": tenant.region, "created_at": tenant.created_at, "via": tenant.created_via})
    savepoint = target.begin_nested()
    try:
        target.execute(text("DELETE FROM tenants WHERE id <> :id"), {"id": tenant.id})
        savepoint.commit()
    except Exception:  # someone else's rows: not a database for one tenant
        savepoint.rollback()
        raise DedicatedDatabaseError("That database holds other tenants' data; give the tenant an empty one")


def _check_suspended(tenant: TenantModel, now) -> None:
    if tenant.status != "suspended" or tenant.suspended_at is None:
        raise DedicatedDatabaseError(f"Suspend {tenant.slug} first: nothing may change while it moves")
    if tenant.suspended_at > now - timedelta(seconds=CACHE_SECONDS):
        raise DedicatedDatabaseError(f"Wait {int(CACHE_SECONDS)} seconds after suspending, so every process "
                                     "has stopped routing the tenant")


def _erase_from(engine: Engine, tenant_id: str) -> int:
    with tenant_scope(tenant_id), Session(engine, info={"tenant_id": tenant_id}) as db, db.begin():
        return sum(db.execute(t.delete()).rowcount for t in reversed(data.tenant_tables()))


def attach(owner_session: Session, slug: str, ref: str, *, now, report: Callable[[str], None] = print) -> dict:
    """Moves a tenant from the shared database into its own."""
    from app.db.session import get_engine

    tenant = owner_session.scalar(select(TenantModel).where(TenantModel.slug == slug))
    if tenant is None:
        raise DedicatedDatabaseError(f"No tenant '{slug}'")
    if tenant.database_ref:
        raise DedicatedDatabaseError(f"{slug} already has its own database")
    try:
        secret_store.check_ref(ref, slug, example="secret://tenant/database_url", first_tenant_slug="\0")
    except secret_store.SecretRefError as exc:
        raise DedicatedDatabaseError(str(exc)) from exc
    _check_suspended(tenant, now)
    app_url, migrations_url = secret_store.resolve(ref, slug), owner_url(ref, slug)
    if not app_url:
        raise DedicatedDatabaseError(f"Set {secret_store.where_to_put(ref, slug)} first")
    report(f"Migrating {slug}'s database")
    migrate(migrations_url)
    target_engine = create_engine(migrations_url)
    try:
        with target_engine.begin() as target:
            if _rows(target, tenant.id):
                raise DedicatedDatabaseError("That database already holds rows of this tenant")
            _prepare(target, tenant)
            with get_engine().connect() as source, source.begin():
                report(f"Copying {slug}'s rows")
                counts = _copy(source, target, tenant.id, report)
    finally:
        target_engine.dispose()
    with _engine(app_url).connect() as check:  # the app role reaches it, under row-level security
        with check.begin():
            copied = _rows(check, tenant.id)
    if copied != sum(counts.values()):
        raise DedicatedDatabaseError(f"Copied {sum(counts.values())} rows but the app role sees {copied}")
    tenant.database_ref = ref
    owner_session.commit()
    forget()
    report("Erasing them from the shared database")
    erased = _erase_from(get_engine(), tenant.id)
    return {"rows": sum(counts.values()), "erased_from_shared": erased, "tables": counts}


def detach(owner_session: Session, slug: str, *, now, report: Callable[[str], None] = print) -> dict:
    """Moves a tenant from its own database back into the shared one."""
    from app.db.session import get_engine, get_owner_engine

    tenant = owner_session.scalar(select(TenantModel).where(TenantModel.slug == slug))
    if tenant is None:
        raise DedicatedDatabaseError(f"No tenant '{slug}'")
    if not tenant.database_ref:
        raise DedicatedDatabaseError(f"{slug} is already in the shared database")
    _check_suspended(tenant, now)
    ref = tenant.database_ref
    app_url = secret_store.resolve(ref, slug)
    if not app_url:
        raise DedicatedDatabaseError(f"Set {secret_store.where_to_put(ref, slug)} first")
    dedicated = _engine(app_url)
    with get_owner_engine().begin() as target:
        if _rows(target, tenant.id):
            raise DedicatedDatabaseError("The shared database already holds rows of this tenant")
        with dedicated.connect() as source, source.begin():
            report(f"Copying {slug}'s rows")
            counts = _copy(source, target, tenant.id, report)
    with get_engine().connect() as check, check.begin():
        copied = _rows(check, tenant.id)
    if copied != sum(counts.values()):
        raise DedicatedDatabaseError(f"Copied {sum(counts.values())} rows but the app role sees {copied}")
    tenant.database_ref = None
    owner_session.commit()
    forget()
    report("Erasing them from its own database (drop that database when you're done with it)")
    erased = _erase_from(dedicated, tenant.id)
    return {"rows": sum(counts.values()), "erased_from_dedicated": erased, "tables": counts}
