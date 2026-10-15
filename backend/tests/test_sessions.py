"""Epic 37: short, rotating sessions (US37.12) and session revocation
(US37.13)."""

import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select, update

from app.auth import sessions
from app.config import Settings
from app.db.models import AuditEventModel, SessionModel, SessionTokenModel
from app.db.session import get_session_factory
from app.main import app

from .conftest import sign_in


def _login(name: str, roles: list[str] | None = None) -> TestClient:
    c = TestClient(app)
    r = sign_in(c, name, roles if roles is not None else ["analyst"])
    assert r.status_code == 200, r.text
    return c


def _with_cookies(**cookies: str) -> TestClient:
    """Another browser holding copies of someone's cookies."""
    return TestClient(app, cookies=cookies)


def _db_update(model, values: dict, *where) -> None:
    with get_session_factory()() as db, db.begin():
        db.execute(update(model).where(*where).values(**values))


def _audit(action: str) -> list[AuditEventModel]:
    with get_session_factory()() as db:
        return list(db.scalars(select(AuditEventModel).where(AuditEventModel.action == action)))


# ---- US37.12: tokens and cookies ----------------------------------------------------


def test_sign_in_sets_two_httponly_cookies_and_stores_only_hashes():
    c = TestClient(app)
    r = sign_in(c, "dana", ["analyst"])
    set_cookies = r.headers.get_list("set-cookie")
    access = next(h for h in set_cookies if h.startswith("asg_session="))
    refresh = next(h for h in set_cookies if h.startswith("asg_refresh="))
    for header in (access, refresh):
        assert "HttpOnly" in header
        assert "SameSite=lax" in header
    assert "Path=/api/auth/refresh" in refresh
    assert "Max-Age=900" in access  # 15 minutes

    access_value, refresh_value = c.cookies.get("asg_session"), c.cookies.get("asg_refresh")
    with get_session_factory()() as db:
        hashes = set(db.scalars(select(SessionTokenModel.token_hash)))
        session_ids = set(db.scalars(select(SessionModel.id)))
    assert hashlib.sha256(access_value.encode()).hexdigest() in hashes
    assert hashlib.sha256(refresh_value.encode()).hexdigest() in hashes
    assert access_value not in hashes | session_ids
    assert refresh_value not in hashes | session_ids
    assert r.json()["session_id"] in session_ids


def test_writes_need_the_csrf_header_reads_dont():
    c = _login("erin")
    token = c.headers.pop("X-CSRF-Token")
    assert c.get("/api/processes").status_code == 200

    missing = c.post("/api/processes", json={"name": "P"})
    assert missing.status_code == 403
    assert missing.headers["X-Auth-Error"] == "csrf"
    assert c.post("/api/processes", json={"name": "P"}, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    assert c.post("/api/processes", json={"name": "P"}, headers={"X-CSRF-Token": token}).status_code == 201
    # /me hands the token back after a page reload.
    assert c.get("/api/auth/me").json()["csrf_token"] == token


def test_refresh_rotates_both_tokens_and_retires_the_old_pair():
    c = _login("frank")
    old_access, old_refresh = c.cookies.get("asg_session"), c.cookies.get("asg_refresh")

    r = c.post("/api/auth/refresh")
    assert r.status_code == 200
    assert r.json()["name"] == "frank"
    assert r.json()["csrf_token"] == c.headers["X-CSRF-Token"]
    assert c.cookies.get("asg_session") != old_access
    assert c.cookies.get("asg_refresh") != old_refresh
    assert c.get("/api/auth/me").status_code == 200
    assert _with_cookies(asg_session=old_access).get("/api/auth/me").status_code == 401


def test_expired_session_cookie_is_refused_and_refresh_recovers():
    c = _login("gina")
    _db_update(
        SessionTokenModel, {"expires_at": sessions._now() - timedelta(seconds=1)}, SessionTokenModel.kind == "access"
    )
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/refresh").status_code == 200
    assert c.get("/api/auth/me").status_code == 200


def test_reused_refresh_token_revokes_the_whole_session(monkeypatch):
    monkeypatch.setattr(sessions, "REUSE_GRACE", timedelta(seconds=-1))
    c = _login("hugo")
    stolen = c.cookies.get("asg_refresh")
    assert c.post("/api/auth/refresh").status_code == 200

    thief = _with_cookies(asg_refresh=stolen)
    assert thief.post("/api/auth/refresh").status_code == 401
    # The legitimate browser is signed out too: one of the two is a thief.
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/refresh").status_code == 401
    events = _audit("refresh_token_reuse")
    assert len(events) == 1
    assert events[0].outcome == "refused"
    assert events[0].actor_name == "hugo"


def test_concurrent_refresh_within_grace_is_not_treated_as_theft():
    c = _login("iris")
    same_token = c.cookies.get("asg_refresh")
    assert c.post("/api/auth/refresh").status_code == 200

    other_tab = _with_cookies(asg_refresh=same_token)
    r = other_tab.post("/api/auth/refresh")
    assert r.status_code == 200
    assert r.headers.get_list("set-cookie") == []  # nothing new handed out
    assert c.get("/api/auth/me").status_code == 200
    assert _audit("refresh_token_reuse") == []


def test_idle_session_expires():
    c = _login("jack")
    _db_update(SessionModel, {"last_used_at": sessions._now() - timedelta(minutes=31)})
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/refresh").status_code == 401


def test_session_cannot_outlive_its_absolute_lifetime():
    c = _login("kim")
    _db_update(SessionModel, {"expires_at": sessions._now() - timedelta(seconds=1)})
    assert c.get("/api/auth/me").status_code == 401
    assert c.post("/api/auth/refresh").status_code == 401


def test_refresh_refused_once_the_user_is_deactivated(client):
    c = _login("leo")
    user_id = c.get("/api/auth/me").json()["id"]
    refresh = c.cookies.get("asg_refresh")
    assert client.post(f"/api/users/{user_id}/deactivate").status_code == 200
    assert _with_cookies(asg_refresh=refresh).post("/api/auth/refresh").status_code == 401


def test_logout_revokes_the_session_and_its_refresh_token():
    c = _login("mia")
    refresh = c.cookies.get("asg_refresh")
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/auth/me").status_code == 401
    assert _with_cookies(asg_refresh=refresh).post("/api/auth/refresh").status_code == 401


# ---- US37.13: seeing and ending sessions ---------------------------------------------


def test_user_sees_and_ends_their_own_sessions():
    laptop = _login("nora")
    phone = _login("nora")
    listed = laptop.get("/api/auth/sessions").json()
    assert len(listed) == 2
    assert [s["current"] for s in listed].count(True) == 1
    other = next(s for s in listed if not s["current"])

    assert laptop.delete(f"/api/auth/sessions/{other['id']}").status_code == 204
    assert phone.get("/api/auth/me").status_code == 401
    assert laptop.get("/api/auth/me").status_code == 200
    assert len(laptop.get("/api/auth/sessions").json()) == 1


def test_user_cannot_end_someone_elses_session():
    owen = _login("owen")
    pia = _login("pia")
    pia_session = pia.get("/api/auth/me").json()["session_id"]
    assert owen.delete(f"/api/auth/sessions/{pia_session}").status_code == 404
    assert pia.get("/api/auth/me").status_code == 200


def test_admin_lists_and_revokes_sessions(client):
    first, second = _login("quinn"), _login("quinn")
    quinn = first.get("/api/auth/me").json()

    users = {u["name"]: u for u in client.get("/api/users").json()}
    assert users["quinn"]["active_sessions"] == 2
    listed = client.get(f"/api/users/{quinn['id']}/sessions").json()
    assert {s["id"] for s in listed} == {quinn["session_id"], second.get("/api/auth/me").json()["session_id"]}

    assert client.delete(f"/api/users/{quinn['id']}/sessions/{quinn['session_id']}").status_code == 204
    assert first.get("/api/auth/me").status_code == 401
    assert second.get("/api/auth/me").status_code == 200

    r = client.post(f"/api/users/{quinn['id']}/sessions/revoke-all")
    assert r.json() == {"revoked": 1}
    assert second.get("/api/auth/me").status_code == 401
    assert {e.action for e in _audit("session_revoked") + _audit("sessions_revoked_all")} == {
        "session_revoked", "sessions_revoked_all",
    }


def test_only_admins_manage_other_peoples_sessions(viewer_client):
    target = _login("rosa")
    rosa = target.get("/api/auth/me").json()
    assert viewer_client.get(f"/api/users/{rosa['id']}/sessions").status_code == 403
    assert viewer_client.post(f"/api/users/{rosa['id']}/sessions/revoke-all").status_code == 403
    assert target.get("/api/auth/me").status_code == 200


# ---- settings ----------------------------------------------------------------------


def test_cookies_are_secure_by_default_only_in_production():
    assert Settings(environment="test").cookie_secure is False
    assert Settings(environment="test", auth_cookie_secure=True).cookie_secure is True


def test_production_refuses_insecure_cookies():
    with pytest.raises(ValidationError, match="AUTH_COOKIE_SECURE"):
        Settings(environment="production", auth_mode="local", auth_cookie_secure=False)
