"""Epic 38, US38.12: an operator creates a tenant and hands it to its first
admin -- by a one-time invitation, or through the tenant's own IdP -- and
it's audited in the platform's log and the tenant's."""

from datetime import timedelta
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.db.models import AuditEventModel, GroupRoleMappingModel, TenantIdentityModel
from app.db.session import get_owner_engine, get_session_factory
from app.ids import new_id
from app.main import app
from app.tenancy import tenant_scope

from .test_oidc import CLIENT_ID, ISSUER
from .test_operator_console import _local_operators, _operator, _platform_events  # noqa: F401

ACME = "acme.test"


def _create(ops, **first_admin):
    return ops.post("/api/operator/tenants", json={"slug": "acme", "display_name": "Acme Corp", "host": ACME,
                                                   **first_admin})


def _follow(url: str) -> tuple[TestClient, object]:
    """The first admin's browser, opening the link on the tenant's host."""
    parts = urlsplit(url)
    browser = TestClient(app, base_url=f"http://{ACME}")
    return browser, browser.get(f"{parts.path}?{parts.query}", follow_redirects=False)


def _tenant_actions(tenant_id: str) -> list[str]:
    with tenant_scope(tenant_id), get_session_factory()() as db:
        return [e.action for e in db.scalars(select(AuditEventModel).order_by(AuditEventModel.at))]


# ---- by invitation -----------------------------------------------------------------


def test_the_first_admin_joins_by_a_one_time_invitation_and_sets_up_sso(client):
    ops = _operator()
    created = _create(ops, first_admin_email="ada@acme.test")
    assert created.status_code == 201, created.text
    acme, url = created.json()["tenant"], created.json()["invitation_url"]
    assert urlsplit(url).hostname == ACME  # on the tenant's own host, where its cookie belongs
    assert acme["created_via"] == "console"

    # No local name picker, even outside production: the link is the only way in.
    stranger = TestClient(app, base_url=f"http://{ACME}")
    assert stranger.get("/api/auth/config").json()["auth_mode"] == "none"
    assert stranger.post("/api/auth/login", json={"name": "Mallory", "roles": ["admin"]}).status_code == 404

    browser, landed = _follow(url)
    assert landed.status_code == 302 and landed.headers["location"].endswith("/directory")
    me = browser.get("/api/auth/me").json()
    assert me["name"] == "ada@acme.test" and [g["role"] for g in me["roles"]] == ["admin"]
    assert browser.get("/api/directory/identity").status_code == 200  # can set up SSO

    _, again = _follow(url)  # once only
    assert again.status_code == 302 and "auth_error=invitation_refused" in again.headers["location"]

    actions = _tenant_actions(acme["id"])
    assert actions[:3] == ["tenant_created", "first_admin_invited", "first_admin_joined"]
    assert [e.action for e in _platform_events("tenant_created")] == ["tenant_created"]
    assert _platform_events("first_admin_invited")[0].detail["email"] == "ada@acme.test"
    listed = {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}
    assert listed["acme"]["first_admin"]["state"] == "joined"


def test_a_console_tenant_can_never_switch_on_local_sign_in(client):
    ops = _operator()
    browser, _ = _follow(_create(ops, first_admin_email="ada@acme.test").json()["invitation_url"])
    browser.headers["X-CSRF-Token"] = browser.get("/api/auth/me").json()["csrf_token"]
    refused = browser.put("/api/directory/identity", json={"auth_mode": "local"})
    assert refused.status_code == 400 and "local sign-in isn't available" in refused.json()["detail"]


def test_an_expired_or_replaced_invitation_is_refused(client):
    ops = _operator()
    created = _create(ops, first_admin_email="ada@acme.test").json()
    first_url, acme_id = created["invitation_url"], created["tenant"]["id"]
    second = ops.post(f"/api/operator/tenants/{acme_id}/invitation", json={"email": "ada@acme.test"})
    assert second.status_code == 200, second.text
    assert "invitation_refused" in _follow(first_url)[1].headers["location"]  # replaced by the new one

    with get_owner_engine().begin() as conn:
        conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": acme_id})
        conn.execute(text("UPDATE tenant_invitations SET expires_at = now() - interval '1 minute'"))
    assert "invitation_refused" in _follow(second.json()["invitation_url"])[1].headers["location"]
    assert {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}["acme"]["first_admin"]["state"] == "expired"


def test_no_invitation_once_the_tenant_has_an_admin_or_sso(client):
    """Otherwise an operator could invite themselves into any tenant."""
    ops = _operator()
    created = _create(ops, first_admin_email="ada@acme.test").json()
    acme_id = created["tenant"]["id"]
    pending = ops.post(f"/api/operator/tenants/{acme_id}/invitation", json={"email": "otto@hosting.test"}).json()
    _follow(created["invitation_url"])  # replaced: refused
    # SSO is set up before the pending link is used: the link no longer works.
    with tenant_scope(acme_id), get_session_factory()() as db, db.begin():
        db.add(TenantIdentityModel(id=new_id("tidp"), auth_mode="oidc", issuer=ISSUER, client_id=CLIENT_ID,
                                   client_secret_ref="secret://tenant/oidc_client_secret"))
    landed = _follow(pending["invitation_url"])[1]
    assert "invitation_refused" in landed.headers["location"]
    again = ops.post(f"/api/operator/tenants/{acme_id}/invitation", json={"email": "otto@hosting.test"})
    assert again.status_code == 409 and "identity provider" in again.json()["detail"]
    assert _platform_events("first_admin_invited")[-1].outcome == "refused"

    # And for a tenant whose first admin has joined.
    with get_owner_engine().begin() as conn:
        conn.execute(text("UPDATE tenant_domains SET host = 'old.acme.test' WHERE host = :h"), {"h": ACME})
    other = ops.post("/api/operator/tenants", json={"slug": "beta", "host": ACME, "first_admin_email": "b@beta.test"})
    beta_id = other.json()["tenant"]["id"]
    _follow(other.json()["invitation_url"])
    joined = ops.post(f"/api/operator/tenants/{beta_id}/invitation", json={"email": "otto@hosting.test"})
    assert joined.status_code == 409 and "already has an admin" in joined.json()["detail"]


# ---- through the tenant's IdP ------------------------------------------------------


IDP = {"issuer": ISSUER, "client_id": CLIENT_ID, "client_secret_ref": "secret://tenant/oidc_client_secret",
       "display_name": "Acme SSO", "acr_values": "mfa", "mfa_acr_values": ["mfa"], "admin_group": "acme-admins"}


def test_with_an_idp_the_admins_group_is_mapped_to_admin(client):
    ops = _operator()
    created = _create(ops, idp=IDP)
    assert created.status_code == 201, created.text
    assert created.json()["invitation_url"] is None
    acme_id = created.json()["tenant"]["id"]
    with tenant_scope(acme_id), get_session_factory()() as db:
        row = db.scalars(select(TenantIdentityModel)).one()
        assert (row.auth_mode, row.issuer, row.client_secret_ref) == ("oidc", ISSUER, IDP["client_secret_ref"])
        assert [(m.group, m.role) for m in db.scalars(select(GroupRoleMappingModel))] == [("acme-admins", "admin")]
    config = TestClient(app, base_url=f"http://{ACME}").get("/api/auth/config").json()
    assert (config["auth_mode"], config["sso_name"]) == ("oidc", "Acme SSO")
    assert _tenant_actions(acme_id)[:2] == ["tenant_created", "identity_settings_changed"]
    assert _platform_events("tenant_idp_configured")[0].detail["admin_group"] == "acme-admins"
    assert {t["slug"]: t for t in ops.get("/api/operator/tenants").json()}["acme"]["first_admin"]["state"] == "sso"


@pytest.mark.parametrize(
    ("first_admin", "reason"),
    [
        ({}, "first admin gets in"),
        ({"first_admin_email": "ada@acme.test", "idp": IDP}, "first admin gets in"),
        ({"first_admin_email": "not-an-email"}, "email address"),
        ({"idp": {**IDP, "client_secret_ref": "env:OPENROUTER_API_KEY"}}, "first organization"),
        ({"idp": {**IDP, "admin_group": " "}}, "admins"),
    ],
)
def test_a_tenant_is_not_created_without_a_way_for_its_first_admin_in(client, first_admin, reason):
    ops = _operator()
    refused = _create(ops, **first_admin)
    assert refused.status_code == 400 and reason in refused.json()["detail"], refused.text
    assert "acme" not in {t["slug"] for t in ops.get("/api/operator/tenants").json()}


def test_the_invitation_lasts_24_hours():
    from app.tenancy.onboarding import INVITATION_LIFETIME

    assert INVITATION_LIFETIME == timedelta(hours=24)
