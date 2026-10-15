"""Epic 37, US37.7-37.8: OIDC sign-in and MFA verification, plus the
IdP's part in sessions (US37.12 refresh confirmation, US37.13 logout).

A fake IdP signs real tokens with a real RSA key and answers discovery,
JWKS and the token endpoint through httpx's MockTransport, so the whole
flow -- PKCE, state, nonce, signature, claims -- runs as it would against
Keycloak or Entra ID."""

import base64
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import oidc
from app.config import get_settings
from app.db.models import AuditEventModel, SessionModel, UserIdentityModel
from app.db.session import get_session_factory
from app.main import app

from .conftest import ALL_ROLES, sign_in

ISSUER = "https://idp.test/realms/axyntro"
CLIENT_ID = "axyntro"
FRONTEND = "http://frontend.test"


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeIdp:
    def __init__(self):
        self.key = _new_key()
        self.kid = "key-1"
        self.codes: dict[str, dict] = {}
        self.refresh_tokens: dict[str, dict] = {}
        self.disabled_subjects: set[str] = set()
        self.down = False
        self.token_overrides: dict = {}
        self.sign_with = None  # (key, alg, headers) to forge a token

    def jwks(self) -> dict:
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"},
                         {"kty": "RSA", "use": "enc", "kid": "enc-1", "n": public["n"], "e": public["e"]}]}

    def sign(self, claims: dict) -> str:
        if self.sign_with is not None:
            key, alg, headers = self.sign_with
            return jwt.encode(claims, key, algorithm=alg, headers=headers)
        pem = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": self.kid})

    def authenticate(self, authorize_url: str, **user) -> str:
        """The user signs in at the IdP; returns the code it redirects back with."""
        params = {k: v[0] for k, v in parse_qs(urlsplit(authorize_url).query).items()}
        code = secrets.token_urlsafe(16)
        self.codes[code] = {"params": params, "user": user}
        return code

    def id_token(self, user: dict, nonce: str | None) -> str:
        now = int(time.time())
        claims = {"iss": ISSUER, "aud": CLIENT_ID, "azp": CLIENT_ID, "iat": now, "exp": now + 300,
                  "sub": user.get("sub", "sub-ana"), "sid": user.get("sid", "idp-session-1"),
                  "email": user.get("email", "ana@example.com"), "name": user.get("name", "Ana Costa"),
                  "preferred_username": user.get("username", "ana"), "acr": user.get("acr", "mfa"),
                  "groups": user.get("groups", ["/axyntro-analysts"])}
        if nonce is not None:
            claims["nonce"] = nonce
        for name in ("amr", "_claim_names"):
            if name in user:
                claims[name] = user[name]
        claims.update(self.token_overrides)
        return self.sign({k: v for k, v in claims.items() if v is not None})

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            return httpx.Response(503)
        url = str(request.url)
        if url == f"{ISSUER}/.well-known/openid-configuration":
            return httpx.Response(200, json={
                "issuer": ISSUER, "authorization_endpoint": f"{ISSUER}/auth", "token_endpoint": f"{ISSUER}/token",
                "jwks_uri": f"{ISSUER}/certs", "end_session_endpoint": f"{ISSUER}/logout",
            })
        if url == f"{ISSUER}/certs":
            return httpx.Response(200, json=self.jwks())
        if url == f"{ISSUER}/token":
            client_id, _, secret = base64.b64decode(request.headers["authorization"].split()[1]).decode().partition(":")
            if (client_id, secret) != (CLIENT_ID, "client-secret"):
                return httpx.Response(401, json={"error": "invalid_client"})
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form["grant_type"] == "authorization_code":
                entry = self.codes.pop(form["code"], None)
                if entry is None:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
                if challenge != entry["params"]["code_challenge"]:
                    return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
                return httpx.Response(200, json=self._tokens(entry["user"], entry["params"]["nonce"]))
            if form["grant_type"] == "refresh_token":
                user = self.refresh_tokens.pop(form["refresh_token"], None)
                if user is None or user.get("sub", "sub-ana") in self.disabled_subjects:
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json=self._tokens(user, None))
        return httpx.Response(404)

    def _tokens(self, user: dict, nonce: str | None) -> dict:
        refresh = secrets.token_urlsafe(16)
        self.refresh_tokens[refresh] = user
        return {"id_token": self.id_token(user, nonce), "refresh_token": refresh, "access_token": "at"}


@pytest.fixture
def idp(monkeypatch):
    fake = FakeIdp()
    env = {
        "AUTH_MODE": "oidc", "OIDC_ISSUER": ISSUER, "OIDC_CLIENT_ID": CLIENT_ID, "OIDC_CLIENT_SECRET": "client-secret",
        "OIDC_DISPLAY_NAME": "Meridian SSO", "OIDC_ACR_VALUES": "mfa", "AUTH_MFA_ACR_VALUES": '["mfa"]',
        "FRONTEND_URL": FRONTEND,
        "OIDC_GROUP_ROLES": json.dumps({
            "axyntro-analysts": ["analyst"], "axyntro-approvers": ["approver"], "axyntro-admins": ["admin"],
            "retail-owners": ["process_owner@Retail Banking"], "typo-group": ["wizard", "approver@Nowhere"],
        }),
    }
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    provider = oidc.OidcProvider(get_settings(), http=httpx.Client(transport=httpx.MockTransport(fake.handle)))
    app.dependency_overrides[oidc.get_oidc_provider] = lambda: provider
    yield fake
    app.dependency_overrides.pop(oidc.get_oidc_provider, None)
    monkeypatch.undo()
    get_settings.cache_clear()


def _sso(idp: FakeIdp, client: TestClient | None = None, return_to: str = "/", **user) -> tuple[TestClient, httpx.Response]:
    """A whole sign-in: our /login, the user at the IdP, our /callback."""
    client = client or TestClient(app)
    start = client.get("/api/auth/oidc/login", params={"return_to": return_to}, follow_redirects=False)
    assert start.status_code == 302, start.text
    code = idp.authenticate(start.headers["location"], **user)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    done = client.get("/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)
    me = client.get("/api/auth/me")
    if me.status_code == 200:
        client.headers["X-CSRF-Token"] = me.json()["csrf_token"]
    return client, done


def _error(response: httpx.Response) -> str | None:
    return parse_qs(urlsplit(response.headers["location"]).query).get("auth_error", [None])[0]


def _audit(action: str) -> list[AuditEventModel]:
    with get_session_factory()() as db:
        return list(db.scalars(select(AuditEventModel).where(AuditEventModel.action == action).order_by(AuditEventModel.at)))


# ---- US37.7: the sign-in ---------------------------------------------------------


def test_sign_in_uses_pkce_state_nonce_and_asks_for_mfa(idp):
    start = TestClient(app).get("/api/auth/oidc/login", follow_redirects=False)
    target = urlsplit(start.headers["location"])
    params = {k: v[0] for k, v in parse_qs(target.query).items()}
    assert f"{target.scheme}://{target.netloc}{target.path}" == f"{ISSUER}/auth"
    assert params["response_type"] == "code"
    assert params["code_challenge_method"] == "S256" and len(params["code_challenge"]) == 43
    assert params["state"] and params["nonce"] and params["state"] != params["nonce"]
    assert params["acr_values"] == "mfa"
    assert params["redirect_uri"] == "http://localhost:8000/api/auth/oidc/callback"
    assert "asg_oidc_state" in start.headers["set-cookie"]


def test_first_sign_in_creates_the_user_with_roles_from_groups(idp):
    client, done = _sso(idp, return_to="/processes/p1", groups=["/axyntro-analysts", "axyntro-approvers", "unmapped"])
    assert done.headers["location"] == f"{FRONTEND}/processes/p1"
    me = client.get("/api/auth/me").json()
    assert me["name"] == "Ana Costa"
    assert {(g["role"], g["source"]) for g in me["roles"]} == {("analyst", "group"), ("approver", "group")}
    assert client.post("/api/processes", json={"name": "P"}).status_code == 201

    event = _audit("sign_in")[-1]
    assert event.outcome == "success" and event.detail["mode"] == "oidc" and event.detail["new_user"] is True
    with get_session_factory()() as db:
        session = db.scalar(select(SessionModel))
        assert (session.auth_method, session.idp_sid) == ("oidc", "idp-session-1")
        assert session.idp_refresh_token and not session.idp_refresh_token.startswith("rt")  # sealed


def test_groups_are_reapplied_at_every_sign_in(idp):
    _sso(idp, groups=["axyntro-approvers"])
    client, _ = _sso(idp, groups=["axyntro-analysts"])
    assert [g["role"] for g in client.get("/api/auth/me").json()["roles"]] == ["analyst"]


def test_business_unit_mapping_and_bad_mappings(client, idp):
    unit = client.post("/api/organization/entities", json={"kind": "business_unit", "name": "Retail Banking"}).json()
    sso, _ = _sso(idp, groups=["retail-owners", "typo-group"])
    roles = sso.get("/api/auth/me").json()["roles"]
    assert [(g["role"], g["scope_type"], g["scope_id"]) for g in roles] == [("process_owner", "business_unit", unit["id"])]
    problems = _audit("sign_in")[-1].detail["mapping_problems"]
    assert any("wizard" in p for p in problems) and any("Nowhere" in p for p in problems)


def test_user_is_matched_by_issuer_and_subject_never_by_email(idp):
    first, _ = _sso(idp, sub="sub-1", email="shared@example.com", name="Pat")
    other, _ = _sso(idp, sub="sub-2", email="shared@example.com", name="Pat")
    renamed, _ = _sso(idp, sub="sub-1", email="pat.new@example.com", name="Pat")
    ids = [c.get("/api/auth/me").json()["id"] for c in (first, other, renamed)]
    assert ids[0] == ids[2] != ids[1]
    names = {c.get("/api/auth/me").json()["name"] for c in (first, other)}
    assert len(names) == 2  # the second Pat gets a distinguishing name
    with get_session_factory()() as db:
        assert db.get(UserIdentityModel, (ISSUER, "sub-1")).email == "pat.new@example.com"


def test_unknown_user_refused_when_jit_provisioning_is_off(idp, monkeypatch):
    monkeypatch.setenv("OIDC_JIT_PROVISIONING", "false")
    get_settings.cache_clear()
    client, done = _sso(idp, sub="stranger")
    assert _error(done) == "not_provisioned"
    assert client.get("/api/auth/me").status_code == 401


def test_deactivated_user_cannot_sign_in(client, idp):
    sso, _ = _sso(idp)
    user_id = sso.get("/api/auth/me").json()["id"]
    assert client.post(f"/api/users/{user_id}/deactivate").status_code == 200
    _, done = _sso(idp)
    assert _error(done) == "deactivated"


def test_groups_overage_keeps_existing_roles(idp):
    _sso(idp, groups=["axyntro-approvers"])
    client, _ = _sso(idp, groups=None, _claim_names={"groups": "src1"})
    assert [g["role"] for g in client.get("/api/auth/me").json()["roles"]] == ["approver"]
    assert _audit("sign_in")[-1].detail["groups_overage"] is True


def test_admin_role_edits_keep_group_roles(client, idp):
    sso, _ = _sso(idp, groups=["axyntro-approvers"])
    user_id = sso.get("/api/auth/me").json()["id"]
    updated = client.put(f"/api/users/{user_id}/roles", json={"roles": [{"role": "auditor"}]}).json()
    assert {(g["role"], g["source"]) for g in updated["roles"]} == {("approver", "group"), ("auditor", "admin")}


def test_local_sign_in_is_off_and_config_names_the_idp(idp):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"name": "x", "roles": ALL_ROLES}).status_code == 404
    config = c.get("/api/auth/config").json()
    assert (config["auth_mode"], config["sso_name"], config["sso_login_url"]) == (
        "oidc", "Meridian SSO", "/api/auth/oidc/login",
    )


def test_sso_endpoints_are_off_in_local_mode():
    c = TestClient(app)
    assert c.get("/api/auth/oidc/login", follow_redirects=False).status_code == 404
    assert c.get("/api/auth/oidc/callback", follow_redirects=False).status_code == 404


def test_return_to_cannot_leave_the_frontend(idp):
    for target in ("//evil.example/x", "https://evil.example", "/\\evil.example"):
        _, done = _sso(idp, return_to=target)
        assert done.headers["location"] == f"{FRONTEND}/"


# ---- refusals --------------------------------------------------------------------


def test_callback_without_this_browsers_state_cookie_is_refused(idp):
    start = TestClient(app).get("/api/auth/oidc/login", follow_redirects=False)
    code = idp.authenticate(start.headers["location"])
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    victim = TestClient(app)  # login CSRF: the attacker's code in the victim's browser
    done = victim.get("/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert _error(done) == "state_mismatch"
    assert victim.get("/api/auth/me").status_code == 401
    assert _audit("sign_in")[-1].outcome == "refused"


def test_state_is_single_use(idp):
    client = TestClient(app)
    start = client.get("/api/auth/oidc/login", follow_redirects=False)
    state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
    code = idp.authenticate(start.headers["location"])
    assert _error(client.get("/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)) is None
    client.cookies.set("asg_oidc_state", state, path="/api/auth/oidc")
    replay = client.get("/api/auth/oidc/callback", params={"code": code, "state": state}, follow_redirects=False)
    assert _error(replay) == "expired"


def test_idp_error_is_reported(idp):
    client = TestClient(app)
    client.get("/api/auth/oidc/login", follow_redirects=False)
    done = client.get("/api/auth/oidc/callback", params={"error": "access_denied"}, follow_redirects=False)
    assert _error(done) == "idp_error"


@pytest.mark.parametrize(
    "tamper",
    ["wrong_nonce", "wrong_audience", "wrong_issuer", "expired", "other_key", "hmac", "unsigned", "unknown_kid"],
)
def test_bad_id_tokens_are_refused(idp, tamper):
    now = int(time.time())
    if tamper == "wrong_nonce":
        idp.token_overrides = {"nonce": "not-the-nonce"}
    elif tamper == "wrong_audience":
        idp.token_overrides = {"aud": "some-other-app"}
    elif tamper == "wrong_issuer":
        idp.token_overrides = {"iss": "https://evil.example"}
    elif tamper == "expired":
        idp.token_overrides = {"exp": now - 3600, "iat": now - 7200}
    elif tamper == "other_key":
        pem = _new_key().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        idp.sign_with = (pem, "RS256", {"kid": idp.kid})
    elif tamper == "hmac":
        idp.sign_with = ("client-secret", "HS256", {"kid": idp.kid})
    elif tamper == "unsigned":
        idp.sign_with = (None, "none", {"kid": idp.kid})
    elif tamper == "unknown_kid":
        pem = idp.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        idp.sign_with = (pem, "RS256", {"kid": "rotated-away"})
    client, done = _sso(idp)
    assert _error(done) == "invalid_token"
    assert client.get("/api/auth/me").status_code == 401


# ---- US37.8: MFA ------------------------------------------------------------------


def test_sign_in_without_mfa_is_refused_and_audited(idp):
    client, done = _sso(idp, acr="1", email="nomfa@example.com")
    assert _error(done) == "mfa_required"
    assert client.get("/api/auth/me").status_code == 401
    event = _audit("sign_in")[-1]
    assert (event.outcome, event.actor_name, event.detail["acr"]) == ("refused", "nomfa@example.com", "1")


def test_amr_method_counts_as_mfa(idp):
    client, done = _sso(idp, acr="1", amr=["pwd", "otp"])
    assert _error(done) is None
    assert client.get("/api/auth/me").status_code == 200


def test_mfa_check_can_be_switched_off_outside_production(idp, monkeypatch):
    monkeypatch.setenv("AUTH_REQUIRE_MFA", "false")
    get_settings.cache_clear()
    _, done = _sso(idp, acr="1")
    assert _error(done) is None


# ---- US37.12/US37.13: the IdP's part in sessions -----------------------------------


def test_refresh_ends_the_session_once_the_idp_account_is_disabled(idp):
    client, _ = _sso(idp, sub="sub-leaver")
    assert client.post("/api/auth/refresh").status_code == 200  # still fine at the IdP
    idp.disabled_subjects.add("sub-leaver")
    assert client.post("/api/auth/refresh").status_code == 401
    with get_session_factory()() as db:
        assert db.scalar(select(SessionModel.revoked_reason)) == "idp_session_ended"


def test_refresh_survives_an_idp_outage(idp):
    client, _ = _sso(idp)
    idp.down = True
    assert client.post("/api/auth/refresh").status_code == 200


def test_logout_returns_the_idp_logout_url(idp):
    client, _ = _sso(idp)
    url = client.post("/api/auth/logout").json()["logout_url"]
    params = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
    assert url.startswith(f"{ISSUER}/logout?")
    assert params["post_logout_redirect_uri"] == f"{FRONTEND}/"
    assert jwt.decode(params["id_token_hint"], options={"verify_signature": False})["sub"] == "sub-ana"


def _logout_token(idp: FakeIdp, **claims) -> str:
    now = int(time.time())
    body = {"iss": ISSUER, "aud": CLIENT_ID, "iat": now, "exp": now + 120, "jti": secrets.token_hex(8),
            "events": {oidc.BACKCHANNEL_LOGOUT_EVENT: {}}, **claims}
    return idp.sign({k: v for k, v in body.items() if v is not None})


def test_backchannel_logout_ends_the_matching_sessions_only(idp):
    laptop, _ = _sso(idp, sid="idp-session-laptop")
    phone, _ = _sso(idp, sid="idp-session-phone")
    response = TestClient(app).post(
        "/api/auth/oidc/backchannel-logout", data={"logout_token": _logout_token(idp, sid="idp-session-laptop", sub="sub-ana")}
    )
    assert response.status_code == 200
    assert laptop.get("/api/auth/me").status_code == 401
    assert phone.get("/api/auth/me").status_code == 200
    assert _audit("idp_logout")[-1].detail["sessions_revoked"] == 1


def test_backchannel_logout_by_subject_ends_all_their_sessions(idp):
    laptop, _ = _sso(idp, sid="a")
    phone, _ = _sso(idp, sid="b")
    TestClient(app).post("/api/auth/oidc/backchannel-logout", data={"logout_token": _logout_token(idp, sub="sub-ana")})
    assert laptop.get("/api/auth/me").status_code == 401
    assert phone.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("bad", ["no_event", "nonce", "no_subject", "forged"])
def test_invalid_logout_tokens_are_rejected(idp, bad):
    client, _ = _sso(idp)
    if bad == "no_event":
        token = _logout_token(idp, sub="sub-ana", events={})
    elif bad == "nonce":
        token = _logout_token(idp, sub="sub-ana", nonce="n")
    elif bad == "no_subject":
        token = _logout_token(idp)
    else:
        pem = _new_key().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
        idp.sign_with = (pem, "RS256", {"kid": idp.kid})
        token = _logout_token(idp, sub="sub-ana")
    response = TestClient(app).post("/api/auth/oidc/backchannel-logout", data={"logout_token": token})
    assert response.status_code == 400
    assert client.get("/api/auth/me").status_code == 200


def test_local_users_are_unaffected_by_sso_helpers():
    c = TestClient(app)
    assert sign_in(c, "local-only", ["analyst"]).status_code == 200
    assert c.post("/api/auth/refresh").status_code == 200
