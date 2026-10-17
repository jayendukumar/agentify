"""Epic 38, US38.14: a live check of a tenant in a database of its own.

Runs inside the API container, against the stack's real Postgres, because
the tenant's database URL is a secret only this process is given:

    docker compose exec -T api python - < testing/tenancy/smoke_dedicated_database.py

Creates a database and a tenant unique to this run; the tenant's admin adds
a process; the tenant is suspended and moved into its own database
(migrated like the shared one); then, active again, it works there --
requests, a job, row-level security -- while the shared database holds
none of its rows. It's moved back, then erased, and the database dropped.

The running API isn't given the secret, so while the tenant is active its
job worker skips that tenant (and logs it) rather than stopping.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import secret_store
from app.config import get_settings
from app.db.models import TenantModel
from app.db.session import get_owner_engine, get_platform_session_factory
from app.ids import utcnow
from app.jobs.worker import run_pending
from app.main import app
from app.operators import tenants as console
from app.tenancy import databases, lifecycle
from app.tenancy.__main__ import main as tenancy_cli
from app.tenancy.data import tenant_tables

results: list[tuple[bool, str]] = []


def check(ok: bool, what: str) -> None:
    results.append((ok, what))
    print(("PASS " if ok else "FAIL ") + what)


def now():
    return utcnow().replace(tzinfo=None)


def rows(engine, tenant_id: str) -> int:
    """As an owner (a superuser here, so filtered by name)."""
    with engine.connect() as conn:
        return sum(conn.scalar(select(func.count()).select_from(t).where(t.c.tenant_id == tenant_id))
                   for t in tenant_tables())


def set_status(tenant_id: str, status: str) -> None:
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == tenant_id).values(
            status=status, suspended_at=now() - timedelta(minutes=5) if status != "active" else None))


def main() -> None:
    settings = get_settings()
    run = time.strftime("%H%M%S")
    slug, host, name = f"smoke-dedi-{run}", f"smoke-dedi-{run}.localhost", f"axyntro_smoke_dedi_{run}"
    owner_url = make_url(settings.database_owner_url).set(database=name).render_as_string(hide_password=False)
    app_url = make_url(settings.database_url).set(database=name).render_as_string(hide_password=False)
    with get_owner_engine().execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    key = secret_store.storage_key(slug, "database_url")
    os.environ[key], os.environ[key + "_OWNER"] = app_url, owner_url
    tenancy_cli(["create", slug, "--name", "Smoke Dedicated", "--host", host])
    with get_platform_session_factory()() as db:
        tenant_id = db.scalar(select(TenantModel.id).where(TenantModel.slug == slug))
    shared_owner, dedicated_owner = get_owner_engine(), create_engine(owner_url)
    try:
        admin = TestClient(app, base_url=f"http://{host}")
        signed_in = admin.post("/api/auth/login", json={"name": "Dee Admin", "roles": ["admin", "analyst"]})
        admin.headers["X-CSRF-Token"] = signed_in.json()["csrf_token"]
        admin.post("/api/processes", json={"name": "Before the move"})
        before = rows(shared_owner, tenant_id)
        check(before > 0, f"the tenant starts in the shared database ({before} rows)")

        set_status(tenant_id, "suspended")
        with Session(get_owner_engine()) as owner:
            moved = databases.attach(owner, slug, "secret://tenant/database_url", now=now(), report=lambda _: None)
        set_status(tenant_id, "active")
        check(moved["rows"] == before, f"moved {moved['rows']} rows into {name}")
        check(rows(shared_owner, tenant_id) == 0, "none left in the shared database")

        check([p["name"] for p in admin.get("/api/processes").json()] == ["Before the move"],
              "the same session sees the same data")
        check(admin.post("/api/processes", json={"name": "After the move"}).status_code == 201, "and writes")
        check(rows(shared_owner, tenant_id) == 0 and rows(dedicated_owner, tenant_id) > moved["rows"],
              "new rows land in its own database only")
        with create_engine(app_url).connect() as conn:  # the app role, no tenant set
            check(conn.scalar(text("SELECT count(*) FROM processes")) == 0,
                  "row-level security is on there too")
        admin.post("/api/organization/exports")
        asyncio.run(run_pending(kinds=["tenant_export"]))
        check(admin.get("/api/organization/exports").json()[0]["status"] == "ready", "a job ran there as the tenant")
        listed = [t for t in console.usage(tenant_id).__dict__.items()]
        check(dict(listed)["processes"] == 2, "the console's counts come from its database")

        set_status(tenant_id, "suspended")
        with Session(get_owner_engine()) as owner:
            back = databases.detach(owner, slug, now=now(), report=lambda _: None)
        check(rows(dedicated_owner, tenant_id) == 0 and rows(shared_owner, tenant_id) == back["rows"],
              f"moved {back['rows']} rows back")
        set_status(tenant_id, "active")
        check(sorted(p["name"] for p in admin.get("/api/processes").json()) == ["After the move", "Before the move"],
              "and it works in the shared database again")
    finally:
        set_status(tenant_id, "suspended")
        with get_platform_session_factory()() as db:
            tenant = db.get(TenantModel, tenant_id)
            lifecycle.change_status(tenant, "delete")
            tenant.delete_after = now() - timedelta(minutes=1)
            db.commit()
            cert = console.erase(db, tenant, operator_name="smoke_dedicated_database")
            db.commit()
        check(rows(shared_owner, tenant_id) == 0, f"erased ({cert['rows_total']} rows)")
        dedicated_owner.dispose()
        for engine in databases._engines.values():
            engine.dispose()
        with get_owner_engine().execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))

    failed = [what for ok, what in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    sys.exit(1 if failed else 0)


main()
