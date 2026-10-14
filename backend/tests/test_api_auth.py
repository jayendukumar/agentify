from fastapi.testclient import TestClient

from app.llm.client import get_llm_client
from app.main import app


def _fresh_client() -> TestClient:
    """A TestClient with no login, for testing the unauthenticated/401
    path -- the `client` fixture is always already logged in."""
    app.dependency_overrides.pop(get_llm_client, None)
    return TestClient(app)


def test_me_without_login_returns_401():
    with _fresh_client() as c:
        r = c.get("/api/auth/me")
        assert r.status_code == 401


def test_login_creates_user_with_roles_and_permissions():
    with _fresh_client() as c:
        r = c.post("/api/auth/login", json={"name": "alice", "roles": ["analyst"]})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "alice"
        assert [g["role"] for g in body["roles"]] == ["analyst"]
        assert body["roles"][0]["scope_type"] == "organization"
        assert "process.edit" in body["permissions"]
        assert "agent.approve" not in body["permissions"]
        assert "asg_session" in c.cookies

        me = c.get("/api/auth/me")
        assert me.status_code == 200
        assert me.json()["id"] == body["id"]


def test_relogin_with_same_name_replaces_local_roles():
    with _fresh_client() as c:
        first = c.post("/api/auth/login", json={"name": "bob", "roles": []})
        assert first.json()["roles"] == []
        assert c.post("/api/processes", json={"name": "Role check"}).status_code == 403
        assert c.post("/api/auth/logout").status_code == 204

        second = c.post("/api/auth/login", json={"name": "bob", "roles": ["analyst"]})
        assert second.json()["id"] == first.json()["id"]
        assert c.post("/api/processes", json={"name": "Role check"}).status_code == 201


def test_logout_clears_session():
    with _fresh_client() as c:
        c.post("/api/auth/login", json={"name": "carol", "roles": ["analyst"]})
        assert c.get("/api/auth/me").status_code == 200

        r = c.post("/api/auth/logout")
        assert r.status_code == 204

        assert c.get("/api/auth/me").status_code == 401


def test_member_without_roles_gets_403_on_a_write_endpoint(client, viewer_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = viewer_client.post("/api/processes", json={"name": "Should not work"})
    assert r.status_code == 403
    assert "Process Analyst" in r.json()["detail"]
    # But a member can still read.
    r = viewer_client.get(f"/api/processes/{process['id']}")
    assert r.status_code == 200


def test_write_endpoint_without_any_login_returns_401():
    with _fresh_client() as c:
        r = c.post("/api/processes", json={"name": "P"})
        assert r.status_code == 401


def test_auth_config_is_public_and_lists_roles():
    with _fresh_client() as c:
        body = c.get("/api/auth/config").json()
        assert body["auth_mode"] == "local"
        assert body["environment"] == "test"
        assert {r["value"] for r in body["roles"]} == {
            "analyst", "architect", "process_owner", "approver", "auditor", "admin",
        }
