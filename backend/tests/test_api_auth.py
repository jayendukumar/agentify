import pytest
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


@pytest.mark.parametrize(
    "initial_role, selected_role, write_status",
    [("viewer", "editor", 201), ("editor", "viewer", 403)],
)
def test_relogin_with_same_name_applies_selected_role(initial_role, selected_role, write_status):
    with _fresh_client() as c:
        first = c.post("/api/auth/login", json={"name": "bob", "role": initial_role})
        assert first.json()["role"] == initial_role
        assert c.post("/api/auth/logout").status_code == 204

        second = c.post("/api/auth/login", json={"name": "bob", "role": selected_role})
        assert second.status_code == 200
        assert second.json()["role"] == selected_role
        assert second.json()["id"] == first.json()["id"]
        assert c.get("/api/auth/me").json()["role"] == selected_role
        assert c.post("/api/processes", json={"name": "Role check"}).status_code == write_status


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
