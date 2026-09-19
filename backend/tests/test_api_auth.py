from fastapi.testclient import TestClient

from app.llm.client import get_llm_client
from app.main import app


def _fresh_client() -> TestClient:
    """A TestClient with no login, for testing the unauthenticated/401
    path -- the `client` fixture is always already logged in as editor."""
    app.dependency_overrides.pop(get_llm_client, None)
    return TestClient(app)


def test_me_without_login_returns_401():
    with _fresh_client() as c:
        r = c.get("/api/auth/me")
        assert r.status_code == 401


def test_login_creates_user_and_sets_cookie():
    with _fresh_client() as c:
        r = c.post("/api/auth/login", json={"name": "alice", "role": "editor"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "alice"
        assert body["role"] == "editor"
        assert "asg_session" in c.cookies

        me = c.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == body["id"]


def test_relogin_with_same_name_ignores_submitted_role():
    with _fresh_client() as c:
        first = c.post("/api/auth/login", json={"name": "bob", "role": "viewer"})
        assert first.json()["role"] == "viewer"

        # Same name, now claiming editor -- must NOT be granted; the
        # original role (set on first login) is authoritative.
        second = c.post("/api/auth/login", json={"name": "bob", "role": "editor"})
        assert second.status_code == 200
        assert second.json()["role"] == "viewer"
        assert second.json()["id"] == first.json()["id"]


def test_logout_clears_session():
    with _fresh_client() as c:
        c.post("/api/auth/login", json={"name": "carol", "role": "editor"})
        assert c.get("/api/auth/me").status_code == 200

        r = c.post("/api/auth/logout")
        assert r.status_code == 204

        assert c.get("/api/auth/me").status_code == 401


def test_viewer_gets_403_on_a_write_endpoint(client, viewer_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = viewer_client.post("/api/processes", json={"name": "Should not work"})
    assert r.status_code == 403
    # But a viewer can still read.
    r = viewer_client.get(f"/api/processes/{process['id']}")
    assert r.status_code == 200


def test_write_endpoint_without_any_login_returns_401():
    with _fresh_client() as c:
        r = c.post("/api/processes", json={"name": "P"})
        assert r.status_code == 401
