"""Epic 38, US38.14: a tenant in a database of its own.

A second database on the test server is migrated like the shared one; a
tenant is moved into it and back out. While there, its requests, jobs and
exports use it, row-level security still applies in it, the other tenants
never notice, and a missing database secret is an error -- never a quiet
fall back to the shared database."""

import asyncio
from datetime import timedelta

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app import secret_store
from app.config import get_settings
from app.db.models import ProcessModel, TenantModel
from app.db.session import get_engine, get_owner_engine
from app.ids import utcnow
from app.jobs.worker import run_pending
from app.main import app
from app.tenancy import databases
from app.tenancy.data import tenant_tables

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in

TENANT_ID, SLUG, HOST = "ten_dedi", "dedi", "dedi.test"
REF = "secret://tenant/database_url"


def _url(base: str) -> str:
    url = make_url(base)
    return url.set(database=f"{url.database}_dedicated").render_as_string(hide_password=False)


APP_URL = _url(get_settings().database_url)
OWNER_URL = _url(get_settings().database_owner_url)


@pytest.fixture(scope="module")
def dedicated_database():
    """Created and migrated once; emptied before each test."""
    name = make_url(OWNER_URL).database
    with get_owner_engine().execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        if not conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}):
            conn.execute(text(f'CREATE DATABASE "{name}"'))
    databases.migrate(OWNER_URL)
    yield
    databases._engines.pop(APP_URL, None)


@pytest.fixture
def dedi(dedicated_database, monkeypatch):
    engine = create_engine(OWNER_URL)
    with engine.begin() as conn:
        tables = ", ".join(t.name for t in reversed(tenant_tables()))
        conn.execute(text(f"TRUNCATE {tables}, tenant_domains, tenants RESTART IDENTITY CASCADE"))
    engine.dispose()
    monkeypatch.setenv("TENANT_DEDI_DATABASE_URL", APP_URL)
    monkeypatch.setenv("TENANT_DEDI_DATABASE_URL_OWNER", OWNER_URL)
    secret_store.get_store.cache_clear()
    databases.forget()
    make_tenant(TENANT_ID, SLUG, [HOST])
    admin = TestClient(app, base_url=f"http://{HOST}")
    assert sign_in(admin, "Dee Admin", ALL_ROLES).status_code == 200
    yield admin
    databases.forget()


def _suspend(status: str = "suspended") -> None:
    """As an operator would, long enough ago for every process to notice."""
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == TENANT_ID).values(
            status=status, suspended_at=utcnow().replace(tzinfo=None) - timedelta(minutes=5)))


def _activate() -> None:
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == TENANT_ID).values(status="active", suspended_at=None))


def _now():
    return utcnow().replace(tzinfo=None)


def _count(engine, tenant_id: str = TENANT_ID) -> int:
    """The tenant's rows in one database, as the app role."""
    with Session(engine) as db:
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
        return sum(db.scalar(select(func.count()).select_from(t)) for t in tenant_tables())


def _attach() -> dict:
    _suspend()
    with Session(get_owner_engine()) as owner:
        result = databases.attach(owner, SLUG, REF, now=_now(), report=lambda _: None)
    _activate()
    return result


def test_a_tenant_moves_into_its_own_database_and_works_there(dedi, client):
    client.post("/api/processes", json={"name": "Meridian onboarding"})
    dedi.post("/api/processes", json={"name": "Dedicated invoicing"})
    ours_before = _count(get_engine(), TEST_TENANT_ID)

    result = _attach()
    assert result["rows"] > 0 and result["erased_from_shared"] == result["rows"]
    dedicated = create_engine(APP_URL)
    assert _count(get_engine()) == 0 and _count(dedicated) == result["rows"]

    # The same session (its row moved too), the same data, and new rows land there.
    assert [p["name"] for p in dedi.get("/api/processes").json()] == ["Dedicated invoicing"]
    assert dedi.post("/api/processes", json={"name": "Second"}).status_code == 201
    with Session(dedicated) as db:
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TENANT_ID})
        assert sorted(db.scalars(select(ProcessModel.name))) == ["Dedicated invoicing", "Second"]
    assert _count(get_engine()) == 0
    # Nobody else noticed.
    assert [p["name"] for p in client.get("/api/processes").json()] == ["Meridian onboarding"]
    assert _count(get_engine(), TEST_TENANT_ID) == ours_before

    # Jobs run there too: an export, made by the worker, as the tenant.
    assert dedi.post("/api/organization/exports").status_code == 202
    assert asyncio.run(run_pending(kinds=["tenant_export"])) == 1
    assert dedi.get("/api/organization/exports").json()[0]["status"] == "ready"
    dedicated.dispose()


def test_row_level_security_stays_on_in_a_dedicated_database(dedi):
    dedi.post("/api/processes", json={"name": "Dedicated invoicing"})
    _attach()
    dedicated = create_engine(APP_URL)
    with Session(dedicated) as db:  # no tenant set: nothing visible, nothing writable
        assert db.scalars(select(ProcessModel)).all() == []
        assert db.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")) is False
    dedicated.dispose()


def test_a_missing_database_secret_fails_rather_than_using_the_shared_database(dedi, monkeypatch):
    _attach()
    monkeypatch.delenv("TENANT_DEDI_DATABASE_URL")
    databases.forget()
    with pytest.raises(databases.DedicatedDatabaseError, match="TENANT_DEDI_DATABASE_URL"):
        dedi.get("/api/processes")


def test_moving_needs_a_settled_suspension_and_an_empty_database(dedi):
    with Session(get_owner_engine()) as owner:
        with pytest.raises(databases.DedicatedDatabaseError, match="Suspend"):
            databases.attach(owner, SLUG, REF, now=_now())
    with get_owner_engine().begin() as conn:
        conn.execute(update(TenantModel).where(TenantModel.id == TENANT_ID).values(
            status="suspended", suspended_at=_now()))
    with Session(get_owner_engine()) as owner:
        with pytest.raises(databases.DedicatedDatabaseError, match="Wait"):
            databases.attach(owner, SLUG, REF, now=_now())
        with pytest.raises(databases.DedicatedDatabaseError, match="reference"):
            databases.attach(owner, SLUG, "env:DATABASE_URL", now=_now() + timedelta(minutes=5))


def test_migrations_run_against_every_tenant_database(dedi, capsys):
    _attach()
    config = Config("alembic.ini")
    config.attributes["configure_logger"] = False
    command.upgrade(config, "head")
    assert "Migrating tenant dedi's own database" in capsys.readouterr().out


def test_a_tenant_moves_back_to_the_shared_database(dedi):
    dedi.post("/api/processes", json={"name": "Dedicated invoicing"})
    moved = _attach()["rows"]
    dedi.post("/api/processes", json={"name": "Made while away"})
    _suspend()
    with Session(get_owner_engine()) as owner:
        result = databases.detach(owner, SLUG, now=_now(), report=lambda _: None)
    _activate()
    assert result["rows"] > moved and result["erased_from_dedicated"] == result["rows"]
    dedicated = create_engine(APP_URL)
    assert _count(dedicated) == 0 and _count(get_engine()) == result["rows"]
    dedicated.dispose()
    assert sorted(p["name"] for p in dedi.get("/api/processes").json()) == ["Dedicated invoicing", "Made while away"]
    # The shared database's sequences moved past the copied ids: new rows insert.
    assert dedi.post("/api/processes", json={"name": "Back home"}).status_code == 201


def test_one_tenants_unreachable_database_holds_up_only_that_tenant(dedi, client, monkeypatch):
    from app.jobs import queue
    from app.jobs.worker import Worker
    from app.tenancy import tenant_scope

    _attach()
    monkeypatch.delenv("TENANT_DEDI_DATABASE_URL")
    databases.forget()
    with tenant_scope(TEST_TENANT_ID), Session(get_engine(), info={"tenant_id": TEST_TENANT_ID}) as db:
        job_id = queue.enqueue(db, "tenant_export", {"export_id": "texp_missing"}, max_attempts=1).id
        db.commit()

    async def tick():
        worker = Worker()
        await worker.tick()
        await asyncio.gather(*(t for group in worker._running.values() for t in group))

    asyncio.run(tick())
    with tenant_scope(TEST_TENANT_ID), Session(get_engine(), info={"tenant_id": TEST_TENANT_ID}) as db:
        db.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TEST_TENANT_ID})
        assert db.scalar(text("SELECT status FROM jobs WHERE id = :j"), {"j": job_id}) != "queued"
