"""Epic 38, US38.8: the operator console and support access.

Operators work on their own host (`ops.localhost`), which reaches no
tenant, through a database role with no privilege on tenant tables
(tests/test_tenancy.py). Inside a tenant they only go as support, under a
grant its admin gave."""

from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.auth import oidc
from app.config import Settings, get_settings
from app.db.models import AuditEventModel, PlatformAuditEventModel, SupportGrantModel
from app.db.session import get_platform_session_factory, get_session_factory
from app.ids import utcnow
from app.main import app
from app.tenancy import tenant_scope

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in
from .test_oidc import CLIENT_ID, ISSUER, FakeIdp

OPS = "http://ops.localhost"


@pytest.fixture(autouse=True)
def _local_operators(monkeypatch):
    """The console is closed by default; these tests sign operators in by name."""
    monkeypatch.setenv("OPERATOR_AUTH_MODE", "local")
    get_settings.cache_clear()
    yield
    monkeypatch.undo()
    get_settings.cache_clear()


def _operator(name: str = "Otto Operator", email: str | None = "otto@hosting.test") -> TestClient:
    ops = TestClient(app, base_url=OPS)
    response = ops.post("/api/operator/login", json={"name": name, "email": email})
    assert response.status_code == 200, response.text
    ops.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return ops


def _platform_events(action: str) -> list[PlatformAuditEventModel]:
    with get_platform_session_factory()() as db:
        return list(db.scalars(select(PlatformAuditEventModel).where(PlatformAuditEventModel.action == action)))


def _tenant_events(action: str, tenant_id: str = TEST_TENANT_ID) -> list[AuditEventModel]:
    with tenant_scope(tenant_id), get_session_factory()() as db:
        return list(db.scalars(select(AuditEventModel).where(AuditEventModel.action == action)
                               .order_by(AuditEventModel.at)))


# ---- the console's own host ------------------------------------------------------------


def test_the_console_and_tenants_never_share_a_host(client):
    ops = TestClient(app, base_url=OPS)
    assert ops.get("/api/host").json() == {"kind": "operator", "name": None}
    assert client.get("/api/host").json() == {"kind": "tenant", "name": "Test"}
    # Tenant routes don't exist on the console's host, and the console's on no tenant's.
    for path in ("/api/processes", "/api/auth/config", "/scim/v2/Users"):
        assert ops.get(path).status_code == 404
    assert client.get("/api/operator/config").status_code == 404
    assert client.post("/api/operator/login", json={"name": "x"}).status_code == 404


def test_an_operator_signs_in_and_sees_tenants_as_counts_only(client):
    client.post("/api/processes", json={"name": "Secret merger", "description": "Project Falcon"})
    make_tenant("ten_northwind", "northwind", ["northwind.test"])
    ops = _operator()
    assert ops.get("/api/operator/me").json()["name"] == "Otto Operator"
    listed = {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}
    assert set(listed) == {"test", "northwind"}
    assert listed["test"]["usage"]["processes"] == 1 and listed["test"]["usage"]["users"] == 1
    assert listed["northwind"]["usage"]["processes"] == 0
    assert "Falcon" not in ops.get("/api/operator/tenants").text and "merger" not in ops.get("/api/operator/tenants").text
    assert _platform_events("operator_sign_in")[0].operator_name == "Otto Operator"


def test_without_signing_in_the_console_gives_nothing():
    ops = TestClient(app, base_url=OPS)
    assert ops.get("/api/operator/tenants").status_code == 401
    signed_in = _operator()
    assert signed_in.post("/api/operator/tenants", json={"slug": "acme", "host": "acme.test"},
                          headers={"X-CSRF-Token": "wrong"}).status_code == 403


def test_a_tenant_session_is_nothing_on_the_console(client):
    cookie = client.cookies.get("asg_session")
    assert TestClient(app, base_url=OPS, cookies={"asg_session": cookie}).get("/api/operator/tenants").status_code == 401


def test_create_suspend_activate_and_delete_a_tenant(client):
    ops = _operator()
    for body, reason in [({"slug": "A!", "host": "x.test"}, "slug"), ({"slug": "test", "host": "new.test"}, "exists"),
                         ({"slug": "acme", "host": "testserver"}, "already reaches"),
                         ({"slug": "acme", "host": "ops.localhost"}, "host name of its own")]:
        refused = ops.post("/api/operator/tenants", json=body)
        assert refused.status_code == 400 and reason in refused.json()["detail"], refused.text
    created = ops.post("/api/operator/tenants", json={"slug": "acme", "display_name": "Acme Corp", "host": "Acme.Test:443"})
    assert created.status_code == 201, created.text
    acme = created.json()
    assert acme["hosts"] == ["acme.test"] and acme["status"] == "active"
    assert TestClient(app, base_url="http://acme.test").get("/api/host").json()["name"] == "Acme Corp"

    assert ops.post(f"/api/operator/tenants/{acme['id']}/delete").status_code == 409  # suspend first
    assert ops.post(f"/api/operator/tenants/{acme['id']}/suspend").json()["status"] == "suspended"
    assert TestClient(app, base_url="http://acme.test").get("/api/auth/config").status_code == 403
    assert ops.post(f"/api/operator/tenants/{acme['id']}/activate").json()["status"] == "active"
    ops.post(f"/api/operator/tenants/{acme['id']}/suspend")
    assert ops.post(f"/api/operator/tenants/{acme['id']}/delete").json()["status"] == "deleting"
    assert TestClient(app, base_url="http://acme.test").get("/api/auth/config").status_code == 403
    assert [e.action for e in _platform_events("tenant_created")] == ["tenant_created"]
    assert _platform_events("tenant_delete")[0].detail == {"slug": "acme", "from": "suspended", "to": "deleting"}


def test_every_operator_write_route_needs_an_operator_session():
    """The tenant route-guard test skips /api/operator: operators have no
    tenant roles. Instead every write there needs a signed-in operator."""
    from fastapi.routing import APIRoute, iter_route_contexts

    from app.api.operator import get_operator_session

    open_routes = {"/api/operator/login"}
    unguarded, checked = [], 0
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.path.startswith("/api/operator/"):
            continue
        if not (route.methods - {"GET", "HEAD", "OPTIONS"}) or route.path in open_routes:
            continue
        checked += 1
        stack, calls = list(route.dependant.dependencies), []
        while stack:
            dep = stack.pop()
            calls.append(dep.call)
            stack.extend(dep.dependencies)
        if get_operator_session not in calls:
            unguarded.append(route.path)
    assert checked >= 4 and unguarded == []


def test_the_console_is_closed_unless_configured(monkeypatch):
    monkeypatch.delenv("OPERATOR_AUTH_MODE")
    get_settings.cache_clear()
    assert Settings(_env_file=None).operator_auth_mode == "none"


def test_local_operator_sign_in_is_refused_in_production():
    with pytest.raises(ValueError, match="OPERATOR_AUTH_MODE=local"):
        Settings(environment="production", auth_mode="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                 oidc_client_secret_env="PATH", operator_auth_mode="local")


# ---- operators through the platform's IdP ------------------------------------------------


@pytest.fixture
def platform_idp(monkeypatch):
    fake = FakeIdp()
    for name, value in {"OPERATOR_AUTH_MODE": "oidc", "OPERATOR_OIDC_ISSUER": ISSUER,
                        "OPERATOR_OIDC_CLIENT_ID": CLIENT_ID, "OPERATOR_OIDC_CLIENT_SECRET": "client-secret",
                        "OPERATOR_OIDC_ACR_VALUES": "mfa", "OPERATOR_MFA_ACR_VALUES": '["mfa"]'}.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    http = httpx.Client(transport=httpx.MockTransport(fake.handle))
    app.dependency_overrides[oidc.get_idp_http] = lambda: http
    yield fake
    app.dependency_overrides.pop(oidc.get_idp_http, None)
    monkeypatch.undo()
    get_settings.cache_clear()


def _operator_sso(fake: FakeIdp, **user) -> tuple[TestClient, httpx.Response]:
    ops = TestClient(app, base_url=OPS)
    start = ops.get("/api/operator/oidc/login", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    assert params["redirect_uri"] == "http://ops.localhost:8000/api/operator/oidc/callback"
    code = fake.authenticate(start.headers["location"], **user)
    return ops, ops.get("/api/operator/oidc/callback", params={"code": code, "state": params["state"]},
                        follow_redirects=False)


def test_operators_sign_in_through_the_platform_idp_with_mfa_and_the_group(platform_idp):
    assert TestClient(app, base_url=OPS).post("/api/operator/login", json={"name": "x"}).status_code == 404
    ops, done = _operator_sso(platform_idp, sub="sub-otto", name="Otto Operator", email="otto@hosting.test",
                              groups=["/platform-operators"])
    assert done.headers["location"] == "http://ops.localhost:3000/"
    me = ops.get("/api/operator/me").json()
    assert me["auth_method"] == "oidc"
    logout_url = ops.post("/api/operator/logout", headers={"X-CSRF-Token": me["csrf_token"]}).json()["logout_url"]
    assert logout_url.startswith(f"{ISSUER}/logout?") and "id_token_hint=" in logout_url
    assert ops.get("/api/operator/me").status_code == 401

    _, refused = _operator_sso(platform_idp, sub="sub-ana", groups=["/axyntro-admins"])
    assert refused.headers["location"].endswith("auth_error=not_an_operator")
    _, refused = _operator_sso(platform_idp, sub="sub-otto", groups=["/platform-operators"], acr="pwd")
    assert refused.headers["location"].endswith("auth_error=mfa_required")
    assert {e.detail["error"] for e in _platform_events("operator_sign_in") if e.outcome == "refused"} == {
        "not_an_operator", "mfa_required"}


# ---- support access ---------------------------------------------------------------------


def _grant(admin: TestClient, **overrides) -> dict:
    body = {"access": "read", "hours": 2, "reason": "Ticket 4711: batch results look wrong", **overrides}
    response = admin.post("/api/directory/support-grants", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _enter(ops: TestClient, grant_id: str, tenant_id: str = TEST_TENANT_ID) -> TestClient:
    """The operator opens the grant from the console and follows the link."""
    entry = ops.post(f"/api/operator/tenants/{tenant_id}/support-grants/{grant_id}/enter")
    assert entry.status_code == 200, entry.text
    url = urlsplit(entry.json()["url"])
    assert url.path == "/api/auth/support/enter"
    browser = TestClient(app, base_url=f"http://{url.hostname}")
    landed = browser.get(f"{url.path}?{url.query}", follow_redirects=False)
    assert landed.status_code == 302 and "auth_error" not in landed.headers["location"], landed.headers
    me = browser.get("/api/auth/me").json()
    browser.headers["X-CSRF-Token"] = me["csrf_token"]
    return browser


def test_read_only_support_sees_changes_nothing_and_everything_is_audited(client):
    client.post("/api/processes", json={"name": "Claims intake", "description": ""})
    ops = _operator()
    assert ops.get(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants").json() == []
    grant = _grant(client)
    assert [g["id"] for g in ops.get(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants").json()] == [grant["id"]]

    otto = _enter(ops, grant["id"])
    me = otto.get("/api/auth/me").json()
    assert me["name"] == "Otto Operator (support)" and me["support"]["access"] == "read"
    assert [r["role"] for r in me["roles"]] == ["auditor"]
    assert [p["name"] for p in otto.get("/api/processes").json()] == ["Claims intake"]
    assert otto.get("/api/audit-events").status_code == 200

    refused = otto.post("/api/processes", json={"name": "Not allowed", "description": ""})
    assert refused.status_code == 403 and "Read-only" in refused.json()["detail"]
    assert otto.post("/api/auth/logout").status_code == 200  # signing out is always allowed

    requests = _tenant_events("support_request")
    assert {(e.detail["method"], e.detail["path"], e.outcome) for e in requests} >= {
        ("GET", "/api/processes", "success"), ("POST", "/api/processes", "refused")}
    assert all(e.detail["operator_email"] == "otto@hosting.test" and e.actor_type == "support" for e in requests)
    assert _tenant_events("support_session_started")[0].detail["operator"] == "Otto Operator"
    assert _platform_events("support_session_opened")[0].detail["grant_id"] == grant["id"]


def test_write_support_works_on_processes_but_never_administers(client):
    otto = _enter(_operator(), _grant(client, access="write")["id"])
    assert otto.post("/api/processes", json={"name": "Fixed by support", "description": ""}).status_code in (200, 201)
    assert otto.get("/api/directory/identity").status_code == 403
    assert otto.post("/api/directory/support-grants", json={"hours": 1, "reason": "more"}).status_code == 403


def test_revoking_or_expiring_the_grant_ends_support_at_the_next_request(client):
    ops = _operator()
    grant = _grant(client)
    otto = _enter(ops, grant["id"])
    revoked = client.post(f"/api/directory/support-grants/{grant['id']}/revoke").json()
    assert revoked["active"] is False and revoked["revoked_by"]
    assert otto.get("/api/processes").status_code == 401
    assert ops.post(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants/{grant['id']}/enter").status_code == 403

    second = _grant(client)
    otto = _enter(ops, second["id"])
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as db, db.begin():
        db.execute(update(SupportGrantModel).where(SupportGrantModel.id == second["id"])
                   .values(expires_at=utcnow().replace(tzinfo=None) - timedelta(minutes=1)))
    assert otto.get("/api/processes").status_code == 401


def test_a_grant_for_one_operator_is_not_anyone_elses(client):
    grant = _grant(client, operator_email="Petra@Hosting.test")
    otto = _operator()
    assert otto.get(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants").json() == []
    assert otto.post(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants/{grant['id']}/enter").status_code == 403
    assert _platform_events("support_session_opened")[0].outcome == "refused"
    _enter(_operator("Petra", "petra@hosting.test"), grant["id"])


def test_a_support_link_works_once_and_only_on_its_tenants_host(client):
    make_tenant("ten_northwind", "northwind", ["northwind.test"])
    ops = _operator()
    grant = _grant(client)
    url = urlsplit(ops.post(f"/api/operator/tenants/{TEST_TENANT_ID}/support-grants/{grant['id']}/enter").json()["url"])
    link = f"{url.path}?{url.query}"
    elsewhere = TestClient(app, base_url="http://northwind.test").get(link, follow_redirects=False)
    assert elsewhere.headers["location"].endswith("auth_error=support_refused")
    assert "auth_error" not in TestClient(app).get(link, follow_redirects=False).headers["location"]
    assert TestClient(app).get(link, follow_redirects=False).headers["location"].endswith("auth_error=support_refused")


def test_an_admin_cannot_widen_a_support_users_roles(client):
    _enter(_operator(), _grant(client)["id"])
    support_user = next(u for u in client.get("/api/users").json() if u["name"].endswith("(support)"))
    refused = client.put(f"/api/users/{support_user['id']}/roles",
                         json={"roles": [{"role": "admin", "scope_type": "organization", "scope_id": ""}]})
    assert refused.status_code == 400 and "support grant" in refused.json()["detail"]


def test_grants_are_limited_in_time_and_to_admins(client, viewer_client):
    too_long = client.post("/api/directory/support-grants", json={"hours": 10_000, "reason": "forever please"})
    assert too_long.status_code == 400
    assert viewer_client.post("/api/directory/support-grants", json={"hours": 1, "reason": "let me"}).status_code == 403


def test_another_tenants_admin_cannot_see_or_revoke_our_grants(client):
    grant = _grant(client)
    make_tenant("ten_northwind", "northwind", ["northwind.test"])
    other = TestClient(app, base_url="http://northwind.test")
    sign_in(other, "Nora", ALL_ROLES)
    assert other.get("/api/directory/support-grants").json() == []
    assert other.post(f"/api/directory/support-grants/{grant['id']}/revoke").status_code == 404
