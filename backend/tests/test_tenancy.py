"""Epic 38, step 1: the tenant boundary (US38.1-38.4). The probe of every
route with another tenant's ids (US38.5) is tests/test_cross_tenant_probe.py."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import NON_TENANT_TABLES, OPERATOR_TABLES, PLATFORM_TABLES, Base, JobModel, ProcessModel, UserModel
from app.db.session import get_engine, get_owner_engine, get_platform_session_factory, get_session_factory
from app.jobs import queue
from app.jobs.worker import handler, run_pending
from app.main import app
from app.tenancy import normalize_host, tenant_scope

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in

OTHER_TENANT_ID = "ten_other"
OTHER_HOST = "northwind.test"


@pytest.fixture
def other_tenant() -> str:
    return make_tenant(OTHER_TENANT_ID, "northwind", [OTHER_HOST])


def _client(host: str, name: str = "someone", roles=None) -> TestClient:
    c = TestClient(app, base_url=f"http://{host}")
    response = sign_in(c, name, ALL_ROLES if roles is None else roles)
    assert response.status_code == 200, response.text
    return c


# ---- US38.2: every table, enforced by the database ---------------------------------


TENANT_TABLES = sorted(name for name in Base.metadata.tables if name not in NON_TENANT_TABLES)


def test_every_tenant_table_has_the_column_in_the_model():
    """Walks the models the way the route-guard test walks routes: a new
    table that forgets TenantScoped fails here."""
    assert len(TENANT_TABLES) > 50
    missing = []
    for name in TENANT_TABLES:
        column = Base.metadata.tables[name].c.get("tenant_id")
        if (
            column is None
            or column.nullable
            or {fk.target_fullname for fk in column.foreign_keys} != {"tenants.id"}
            or "app_current_tenant()" not in str(column.server_default.arg)
        ):
            missing.append(name)
    assert missing == []


def test_every_tenant_table_has_forced_row_level_security_in_the_database():
    with get_owner_engine().connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, p.qual, p.with_check "
                "FROM pg_class c LEFT JOIN pg_policies p ON p.tablename = c.relname AND p.policyname = 'tenant_isolation' "
                "WHERE c.relnamespace = 'public'::regnamespace AND c.relkind = 'r'"
            )
        ).all()
    found = {r.relname: r for r in rows}
    unprotected = [
        name
        for name in TENANT_TABLES
        if not (
            name in found
            and found[name].relrowsecurity
            and found[name].relforcerowsecurity
            and "app_current_tenant()" in (found[name].qual or "")
            and "app_current_tenant()" in (found[name].with_check or "")
        )
    ]
    assert unprotected == []
    # A table in the database the models don't know is a table nobody checked.
    assert set(found) - set(Base.metadata.tables) == {"alembic_version"}


def test_the_app_role_is_held_to_row_level_security_and_cannot_write_platform_tables():
    with get_engine().connect() as conn:
        role = conn.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        ).one()
        assert (role.rolsuper, role.rolbypassrls) == (False, False)
        owns = conn.scalar(
            text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' AND tableowner = current_user")
        )
        assert owns == 0
        for table in PLATFORM_TABLES:
            assert conn.scalar(text(f"SELECT has_table_privilege('{table}', 'SELECT')"))
            assert not conn.scalar(text(f"SELECT has_table_privilege('{table}', 'INSERT, UPDATE, DELETE, TRUNCATE')"))
        assert not conn.scalar(text("SELECT has_table_privilege('processes', 'TRUNCATE')"))
        # US38.8: the operator console's tables are out of the app's reach entirely.
        for table in OPERATOR_TABLES:
            assert not conn.scalar(text(f"SELECT has_table_privilege('{table}', 'SELECT, INSERT, UPDATE, DELETE')"))


def test_the_platform_role_cannot_touch_any_tenant_table():
    """US38.8: the operator console's connection has no privilege on tenant
    data at all, row-level security or not, and can't bypass it."""
    with get_platform_session_factory()() as db:
        role = db.execute(text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")).one()
        assert (role.rolsuper, role.rolbypassrls) == (False, False)
        readable = [
            t for t in TENANT_TABLES
            if db.scalar(text(f"SELECT has_table_privilege('{t}', 'SELECT, INSERT, UPDATE, DELETE')"))
        ]
        assert readable == []
        assert db.scalar(text("SELECT has_table_privilege('tenants', 'INSERT, UPDATE')"))
        # US38.13: it removes an erased tenant's own row (only once the
        # tenant's rows are gone: foreign keys), and may only rewrite an
        # audit event's detail, to strip an erased tenant's personal data.
        assert db.scalar(text("SELECT has_table_privilege('tenants', 'DELETE')"))
        assert not db.scalar(text("SELECT has_table_privilege('tenants', 'TRUNCATE')"))
        assert not db.scalar(text("SELECT has_table_privilege('platform_audit_events', 'UPDATE, DELETE')"))
        assert db.scalar(text("SELECT has_column_privilege('platform_audit_events', 'detail', 'UPDATE')"))
        assert not db.scalar(text("SELECT has_column_privilege('platform_audit_events', 'action', 'UPDATE')"))


def test_without_a_tenant_nothing_is_visible_and_nothing_can_be_written(client):
    client.post("/api/processes", json={"name": "Onboarding"})
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        assert session.scalar(select(ProcessModel.id)) is not None

    # A session opened outside any tenant: the edge that forgot to set one.
    from app.tenancy.context import reset_current_tenant, set_current_tenant

    token = set_current_tenant(None)
    try:
        with get_session_factory()() as session:
            assert session.scalars(select(ProcessModel)).all() == []
            assert session.scalars(select(UserModel)).all() == []
            session.add(ProcessModel(id="proc_orphan", name="Orphan"))
            with pytest.raises(Exception, match="row-level security|null value"):
                session.flush()
    finally:
        reset_current_tenant(token)


def test_a_transaction_cannot_insert_into_another_tenant(other_tenant):
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        session.execute(text("SELECT 1"))
        with pytest.raises(Exception, match="row-level security"):
            session.execute(
                text("INSERT INTO processes (id, name, tenant_id) VALUES ('proc_x', 'X', :t)"), {"t": OTHER_TENANT_ID}
            )


def test_the_tenant_setting_ends_with_the_transaction():
    """SET LOCAL, so a pooled connection never carries a tenant over."""
    with get_engine().connect() as conn:
        with conn.begin():
            conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": TEST_TENANT_ID})
            assert conn.scalar(text("SELECT app_current_tenant()")) == TEST_TENANT_ID
        with conn.begin():
            assert conn.scalar(text("SELECT app_current_tenant()")) is None


# ---- US38.1: tenants and their hosts --------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("Meridian.Localhost:3000", "meridian.localhost"), ("localhost", "localhost"), ("[::1]:8000", "::1"), ("acme.example.", "acme.example")],
)
def test_hosts_are_matched_without_port_or_case(raw, expected):
    assert normalize_host(raw) == expected


def test_an_unknown_host_gets_a_plain_404_that_names_no_tenant():
    c = TestClient(app, base_url="http://nobody.test")
    for path in ("/api/auth/me", "/api/processes", "/scim/v2/Users"):
        response = c.get(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not found"}
    login = c.post("/api/auth/login", json={"name": "x", "roles": []})
    assert login.status_code == 404


def test_health_answers_on_any_host_and_says_isolation_is_enforced():
    response = TestClient(app, base_url="http://10.0.0.7").get("/healthz")
    assert response.status_code == 200
    assert response.json()["tenant_isolation"] == "enforced"


def test_a_suspended_tenant_is_refused_with_a_403():
    make_tenant("ten_paused", "paused", ["paused.test"], status="suspended")
    response = TestClient(app, base_url="http://paused.test").post("/api/auth/login", json={"name": "x", "roles": []})
    assert response.status_code == 403
    assert "suspended" in response.json()["detail"]


def test_hosts_reach_their_own_tenant_with_its_own_data(client, other_tenant):
    client.post("/api/processes", json={"name": "Meridian onboarding"})
    northwind = _client(OTHER_HOST)
    northwind.post("/api/processes", json={"name": "Northwind invoicing"})

    assert [p["name"] for p in client.get("/api/processes").json()] == ["Meridian onboarding"]
    assert [p["name"] for p in northwind.get("/api/processes").json()] == ["Northwind invoicing"]
    # US38.4: one organization per tenant.
    assert client.get("/api/organization").json()["id"] != northwind.get("/api/organization").json()["id"]


def test_names_are_unique_per_tenant_not_per_deployment(client, other_tenant):
    northwind = _client(OTHER_HOST, name="test-editor")
    assert northwind.get("/api/auth/me").json()["id"] != client.get("/api/auth/me").json()["id"]

    body = {"name": "policy-library", "type": "folder", "config": {"path": "."}}
    assert client.post("/api/organization/connectors", json=body).status_code == 201
    assert northwind.post("/api/organization/connectors", json=body).status_code == 201
    assert client.post("/api/organization/connectors", json=body).status_code != 201  # still unique within a tenant


# ---- US38.3: sessions, jobs and background work ---------------------------------------


def test_a_session_cookie_is_refused_on_another_tenants_host(client, other_tenant):
    northwind = TestClient(app, base_url=f"http://{OTHER_HOST}")
    northwind.cookies.set("asg_session", client.cookies.get("asg_session"))
    assert northwind.get("/api/auth/me").status_code == 401
    assert northwind.get("/api/processes").status_code == 401


def test_a_scim_token_is_refused_on_another_tenants_host(client, other_tenant):
    token = client.post("/api/directory/scim/tokens", json={"label": "Entra ID"}).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert TestClient(app).get("/scim/v2/Users", headers=headers).status_code == 200
    assert TestClient(app, base_url=f"http://{OTHER_HOST}").get("/scim/v2/Users", headers=headers).status_code == 401


def test_refusals_are_audited_in_the_tenant_they_happened_in(client, other_tenant):
    viewer = _client(OTHER_HOST, name="viewer", roles=[])
    assert viewer.post("/api/processes", json={"name": "Nope"}).status_code == 403
    admin = _client(OTHER_HOST, name="admin")
    refused = admin.get("/api/audit-events", params={"outcome": "refused"}).json()
    assert any(e["actor_name"] == "viewer" for e in refused)
    assert not any(e["actor_name"] == "viewer" for e in client.get("/api/audit-events", params={"outcome": "refused"}).json())


_seen: list[tuple[str, str | None]] = []


@handler("test_tenancy_echo")
async def _echo(payload, session):
    _seen.append((payload["n"], session.scalar(text("SELECT app_current_tenant()"))))
    return {"rows": session.scalar(select(text("count(*)")).select_from(ProcessModel))}


def test_jobs_run_as_their_own_tenant_and_a_suspended_tenants_jobs_wait(client, other_tenant):
    _seen.clear()
    client.post("/api/processes", json={"name": "Only in the test tenant"})
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        mine = queue.enqueue(session, "test_tenancy_echo", {"n": "test"}).id
        session.commit()
    with tenant_scope(OTHER_TENANT_ID), get_session_factory()() as session:
        theirs = queue.enqueue(session, "test_tenancy_echo", {"n": "other"}).id
        session.commit()
    make_tenant("ten_paused", "paused", ["paused.test"], status="suspended")
    with tenant_scope("ten_paused"), get_session_factory()() as session:
        paused = queue.enqueue(session, "test_tenancy_echo", {"n": "paused"}).id
        session.commit()

    assert asyncio.run(run_pending(kinds=["test_tenancy_echo"])) == 2
    assert sorted(_seen) == [("other", OTHER_TENANT_ID), ("test", TEST_TENANT_ID)]
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as session:
        assert session.get(JobModel, mine).result == {"rows": 1}
        assert session.get(JobModel, theirs) is None  # another tenant's job is invisible here
    with tenant_scope(OTHER_TENANT_ID), get_session_factory()() as session:
        assert session.get(JobModel, theirs).result == {"rows": 0}
    with tenant_scope("ten_paused"), get_session_factory()() as session:
        assert session.get(JobModel, paused).status == "queued"
