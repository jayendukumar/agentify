"""Epic 38, US38.6: each tenant brings its own identity provider.

The test tenant is the first tenant (TENANT_DEFAULT_SLUG), so it keeps the
OIDC_* environment variables. Northwind configures its own IdP on the
Directory page, and signs in through it, while the test tenant is
untouched."""

import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import oidc
from app.config import Settings
from app.db.models import AuditEventModel, TenantIdentityModel, UserModel
from app.db.session import get_session_factory
from app.main import app
from app.tenancy import identity, tenant_scope
from app.tenancy.__main__ import main as tenancy_cli

from .conftest import ALL_ROLES, TEST_TENANT_ID, make_tenant, sign_in
from .test_oidc import CLIENT_ID, ISSUER, FakeIdp

NORTHWIND_ID = "ten_northwind"
NORTHWIND = "northwind.test"
SECRET_REF = "secret://tenant/oidc_client_secret"  # US38.10: TENANT_NORTHWIND_OIDC_CLIENT_SECRET


def _settings(**overrides) -> dict:
    return {
        "auth_mode": "oidc", "issuer": ISSUER, "client_id": CLIENT_ID, "client_secret_ref": SECRET_REF,
        "display_name": "Northwind SSO", "acr_values": "mfa", "mfa_acr_values": ["mfa"], **overrides,
    }


@pytest.fixture
def fake_idp(monkeypatch):
    """Northwind's IdP: real signed tokens, served over a mock transport,
    for the connection test and for sign-in alike."""
    fake = FakeIdp()
    monkeypatch.setenv("TENANT_NORTHWIND_OIDC_CLIENT_SECRET", "client-secret")
    http = httpx.Client(transport=httpx.MockTransport(fake.handle))
    app.dependency_overrides[oidc.get_idp_http] = lambda: http
    app.dependency_overrides[oidc.get_oidc_provider] = (
        lambda config=Depends(identity.get_identity): oidc.OidcProvider(config, http=http)
    )
    yield fake
    app.dependency_overrides.pop(oidc.get_idp_http, None)
    app.dependency_overrides.pop(oidc.get_oidc_provider, None)


@pytest.fixture
def northwind_admin() -> TestClient:
    make_tenant(NORTHWIND_ID, "northwind", [NORTHWIND])
    admin = TestClient(app, base_url=f"http://{NORTHWIND}")
    assert sign_in(admin, "Nora Admin", ALL_ROLES).status_code == 200
    return admin


# ---- defaults ------------------------------------------------------------------------


def test_the_first_tenant_keeps_the_environment_and_others_have_no_sso(client, northwind_admin):
    ours = client.get("/api/directory/identity").json()
    assert (ours["source"], ours["auth_mode"]) == ("environment", "local")
    theirs = northwind_admin.get("/api/directory/identity").json()
    assert (theirs["source"], theirs["auth_mode"]) == ("unset", "local")
    # What to register at the IdP comes from the tenant's own host.
    assert theirs["redirect_uri"] == "http://northwind.test:8000/api/auth/oidc/callback"
    assert theirs["post_logout_redirect_uri"] == "http://northwind.test:3000/"
    assert theirs["scim_base_url"] == "http://northwind.test:8000/scim/v2"
    assert theirs["secret_location"] == "TENANT_NORTHWIND_OIDC_CLIENT_SECRET (environment variable)"


def test_in_production_a_tenant_without_settings_cannot_sign_in_at_all(monkeypatch):
    make_tenant(NORTHWIND_ID, "northwind", [NORTHWIND])
    production = Settings(environment="production", auth_mode="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                          oidc_client_secret_env="PATH", tenant_default_slug="test")
    monkeypatch.setattr(identity, "get_settings", lambda: production)
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        assert identity.resolve(db, host=NORTHWIND).auth_mode == "none"
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as db:
        assert identity.resolve(db, host="testserver").auth_mode == "oidc"


# ---- saving, with the connection tested ---------------------------------------------


def test_a_tenant_admin_tests_saves_and_signs_in_through_their_own_idp(client, northwind_admin, fake_idp):
    tested = northwind_admin.post("/api/directory/identity/test", json=_settings())
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True and tested.json()["signing_keys"] == 1
    assert tested.json()["token_endpoint"] == f"{ISSUER}/token"

    saved = northwind_admin.put("/api/directory/identity", json=_settings())
    assert saved.status_code == 200, saved.text
    assert (saved.json()["source"], saved.json()["auth_mode"]) == ("tenant", "oidc")
    assert saved.json()["secret_configured"] is True

    config = TestClient(app, base_url=f"http://{NORTHWIND}").get("/api/auth/config").json()
    assert (config["auth_mode"], config["sso_name"]) == ("oidc", "Northwind SSO")
    assert client.get("/api/auth/config").json()["auth_mode"] == "local"  # the test tenant is unchanged
    assert TestClient(app, base_url=f"http://{NORTHWIND}").post(
        "/api/auth/login", json={"name": "x", "roles": []}).status_code == 404

    # The whole sign-in, on Northwind's host.
    browser = TestClient(app, base_url=f"http://{NORTHWIND}")
    start = browser.get("/api/auth/oidc/login", follow_redirects=False)
    params = {k: v[0] for k, v in parse_qs(urlsplit(start.headers["location"]).query).items()}
    assert params["redirect_uri"] == "http://northwind.test:8000/api/auth/oidc/callback"
    assert params["acr_values"] == "mfa"
    code = fake_idp.authenticate(start.headers["location"], sub="sub-nora", name="Nora Lund", email="nora@northwind.test")
    done = browser.get("/api/auth/oidc/callback", params={"code": code, "state": params["state"]}, follow_redirects=False)
    assert done.headers["location"] == "http://northwind.test:3000/"
    assert browser.get("/api/auth/me").json()["name"] == "Nora Lund"

    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as db:
        assert db.scalar(select(UserModel).where(UserModel.name == "Nora Lund")) is None
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        event = db.scalar(select(AuditEventModel).where(AuditEventModel.action == "identity_settings_changed"))
        assert event.actor_name == "Nora Admin"
        assert event.detail["previous_source"] == "unset"
        assert event.detail["changed"]["auth_mode"] == {"from": "local", "to": "oidc"}


def test_a_failed_connection_test_saves_nothing(northwind_admin, fake_idp):
    fake_idp.down = True
    tested = northwind_admin.post("/api/directory/identity/test", json=_settings()).json()
    assert tested["ok"] is False and "Couldn't reach the IdP" in tested["problems"][0]
    refused = northwind_admin.put("/api/directory/identity", json=_settings())
    assert refused.status_code == 400 and "connection test failed" in refused.json()["detail"]
    assert northwind_admin.get("/api/directory/identity").json()["source"] == "unset"


def test_an_issuer_the_idp_does_not_claim_is_refused(northwind_admin, fake_idp):
    other = _settings(issuer="https://idp.test/realms/other", discovery_url=f"{ISSUER}/.well-known/openid-configuration")
    tested = northwind_admin.post("/api/directory/identity/test", json=other).json()
    assert tested["ok"] is False and "names issuer" in tested["problems"][0]


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"client_secret_ref": "env:OPENROUTER_API_KEY"}, "only for the deployment's first organization"),
        ({"client_secret_ref": "env:TENANT_TEST_OIDC_CLIENT_SECRET"}, "only for the deployment's first organization"),
        ({"client_secret_ref": "secret://tenant/../test/x"}, "lowercase letters"),
        ({"client_secret_ref": "client-secret"}, "not the secret itself"),
        ({"issuer": ""}, "needs the issuer"),
        ({"scopes": "profile email"}, "must include openid"),
        ({"mfa_acr_values": [], "mfa_amr_values": []}, "what counts as MFA"),
        ({"issuer": "ftp://idp.test"}, "http(s) URL"),
    ],
)
def test_settings_that_could_leak_or_lock_out_are_refused(northwind_admin, fake_idp, overrides, reason):
    response = northwind_admin.put("/api/directory/identity", json=_settings(**overrides))
    assert response.status_code == 400 and reason in response.json()["detail"]


def test_only_admins_see_or_change_the_identity_provider(northwind_admin):
    viewer = TestClient(app, base_url=f"http://{NORTHWIND}")
    sign_in(viewer, "Nils Viewer", ["analyst"])
    assert viewer.get("/api/directory/identity").status_code == 403
    assert viewer.put("/api/directory/identity", json=_settings()).status_code == 403


def test_production_refuses_local_sign_in_plain_http_and_private_addresses():
    production = Settings(environment="production", auth_mode="oidc", oidc_issuer=ISSUER, oidc_client_id=CLIENT_ID,
                          oidc_client_secret_env="PATH")
    base = {k: v for k, v in {**identity.from_settings(production).__dict__}.items() if k in identity.EDITABLE}
    base.update(mfa_amr_values=["mfa"], mfa_acr_values=[], client_secret_ref=SECRET_REF)
    for values, reason in [
        ({**base, "auth_mode": "local"}, "Local sign-in"),
        ({**base, "issuer": "http://idp.example.com"}, "must use https"),
        ({**base, "issuer": "https://127.0.0.1/realms/x"}, "private address"),
        ({**base, "issuer": "https://10.1.2.3/realms/x"}, "private address"),
        ({**base, "require_mfa": False}, "can't be turned off"),
    ]:
        with pytest.raises(identity.IdentityError, match=reason):
            identity.validate(values, "northwind", production)


def test_the_first_tenant_may_keep_the_secret_the_operator_configured():
    settings = Settings(tenant_default_slug="test", oidc_client_secret_env="OIDC_CLIENT_SECRET")
    identity.check_secret_ref("env:OIDC_CLIENT_SECRET", "test", settings)
    with pytest.raises(identity.IdentityError):
        identity.check_secret_ref("env:OIDC_CLIENT_SECRET", "northwind", settings)


def test_each_tenant_gets_its_own_provider_and_caches():
    a = identity.IdentityConfig(auth_mode="oidc", source="tenant", tenant_id="ten_a", issuer=ISSUER, client_id="x")
    b = identity.IdentityConfig(auth_mode="oidc", source="tenant", tenant_id="ten_b", issuer=ISSUER, client_id="x")
    assert oidc.provider_for(a) is oidc.provider_for(a)
    assert oidc.provider_for(a) is not oidc.provider_for(b)


def test_an_operator_can_set_and_undo_a_tenants_identity_provider(northwind_admin, capsys):
    tenancy_cli(["identity", "set", "northwind", "--issuer", ISSUER, "--client-id", CLIENT_ID,
                 "--secret-ref", SECRET_REF, "--admin-group", "northwind-admins"])
    assert northwind_admin.get("/api/directory/group-mappings").json()[0]["group"] == "northwind-admins"
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        assert db.scalar(select(TenantIdentityModel)).issuer == ISSUER
    tenancy_cli(["identity", "reset", "northwind"])
    assert "removed" in capsys.readouterr().out
    assert northwind_admin.get("/api/directory/identity").json()["source"] == "unset"


# ---- US38.7: back-channel logout finds the tenant from the token ----------------------


def _nora_signed_in(fake_idp, northwind_admin, sid: str = "nw-session") -> TestClient:
    assert northwind_admin.put("/api/directory/identity", json=_settings()).status_code == 200
    browser = TestClient(app, base_url=f"http://{NORTHWIND}")
    start = browser.get("/api/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    code = fake_idp.authenticate(start.headers["location"], sub="sub-nora", name="Nora Lund", sid=sid)
    browser.get("/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert browser.get("/api/auth/me").status_code == 200
    return browser


def _logout(fake_idp, **claims) -> str:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": secrets.token_hex(8),
            "events": {oidc.BACKCHANNEL_LOGOUT_EVENT: {}}, "sid": "nw-session", **claims}
    return fake_idp.sign(body)


def test_backchannel_logout_reaches_the_tenant_named_by_the_issuer_on_any_host(client, northwind_admin, fake_idp):
    nora = _nora_signed_in(fake_idp, northwind_admin)
    # The IdP calls the platform's internal host (here: the test tenant's), as Keycloak in Docker calls `api`.
    response = TestClient(app).post("/api/auth/oidc/backchannel-logout", data={"logout_token": _logout(fake_idp)})
    assert response.status_code == 200
    assert nora.get("/api/auth/me").status_code == 401
    with tenant_scope(NORTHWIND_ID), get_session_factory()() as db:
        event = db.scalar(select(AuditEventModel).where(AuditEventModel.action == "idp_logout"))
        assert event.detail["sessions_revoked"] == 1
    with tenant_scope(TEST_TENANT_ID), get_session_factory()() as db:
        assert db.scalar(select(AuditEventModel).where(AuditEventModel.action == "idp_logout")) is None


@pytest.mark.parametrize("bad", ["other_client", "other_issuer", "forged", "garbage"])
def test_a_logout_token_no_tenant_trusts_ends_nothing(northwind_admin, fake_idp, bad):
    nora = _nora_signed_in(fake_idp, northwind_admin)
    if bad == "other_client":
        token = _logout(fake_idp, aud="someone-elses-client")
    elif bad == "other_issuer":
        token = _logout(fake_idp, iss="https://idp.test/realms/other")
    elif bad == "forged":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        fake_idp.sign_with = (pem, "RS256", {"kid": fake_idp.kid})
        token = _logout(fake_idp)
    else:
        token = "not-a-jwt"
    response = TestClient(app).post("/api/auth/oidc/backchannel-logout", data={"logout_token": token})
    assert response.status_code == 400
    assert nora.get("/api/auth/me").status_code == 200
