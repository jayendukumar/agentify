"""Epic 37, US37.10: the inbound SCIM 2.0 server, and US37.11's group
mappings as SCIM membership changes drive them.

`tests/fixtures/scim/` holds the request sequences Entra ID and Okta send,
transcribed from their published SCIM documentation (each file names its
source); they're replayed here step by step."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AuditEventModel, SessionModel
from app.db.session import get_session_factory
from app.main import app

from .conftest import sign_in

FIXTURES = Path(__file__).parent / "fixtures" / "scim"
SCIM_JSON = "application/scim+json"


@pytest.fixture
def scim(client) -> TestClient:
    """An IdP's provisioning client, with a token an admin generated."""
    issued = client.post("/api/directory/scim/tokens", json={"label": "Entra ID"})
    assert issued.status_code == 201, issued.text
    return TestClient(app, headers={"Authorization": f"Bearer {issued.json()['token']}", "Content-Type": SCIM_JSON})


def _lookup(document, path: str):
    if path.startswith("urn:"):
        urn, _, leaf = path.rpartition(".")
        return (document.get(urn) or {}).get(leaf, "__absent__")
    value = document
    for part in path.split("."):
        if isinstance(value, list):
            value = value[int(part)] if int(part) < len(value) else "__absent__"
        elif isinstance(value, dict):
            value = value.get(part, "__absent__")
        else:
            return "__absent__"
    return value


def _fill(value, captured: dict[str, str]):
    text = json.dumps(value)
    for name, captured_value in captured.items():
        text = text.replace("{{" + name + "}}", captured_value)
    return json.loads(text)


def replay(scim: TestClient, fixture: str) -> dict[str, str]:
    captured: dict[str, str] = {}
    for step in json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))["steps"]:
        path = _fill(step["path"], captured)
        kwargs = {"content": json.dumps(_fill(step["body"], captured))} if "body" in step else {}
        response = scim.request(step["method"], f"/scim/v2{path}", **kwargs)
        assert response.status_code == step["status"], f"{step['name']}: {response.status_code} {response.text}"
        if response.status_code != 204:
            assert response.headers["content-type"].startswith(SCIM_JSON), step["name"]
        body = response.json() if response.content else {}
        for name, field in step.get("capture", {}).items():
            captured[name] = body[field]
        for path_, expected in _fill(step.get("expect", {}), captured).items():
            assert _lookup(body, path_) == expected, f"{step['name']}: {path_} = {_lookup(body, path_)!r}, expected {expected!r}"
    return captured


# ---- the vendors' sequences ---------------------------------------------------------


def test_entra_id_sequence(scim):
    replay(scim, "entra_id.json")


def test_okta_sequence(scim):
    replay(scim, "okta.json")


def test_okta_password_is_never_stored(scim):
    replay(scim, "okta.json")
    with get_session_factory()() as db:
        from app.db.models import UserModel

        stored = [u.scim_profile for u in db.scalars(select(UserModel).where(UserModel.provisioned_by == "scim"))]
    assert stored and all("password" not in json.dumps(p) for p in stored)


# ---- the endpoint ---------------------------------------------------------------------


def test_requests_need_a_live_token(client, scim):
    assert TestClient(app).get("/scim/v2/Users").status_code == 401
    wrong = TestClient(app, headers={"Authorization": "Bearer nope"}).get("/scim/v2/Users")
    assert wrong.status_code == 401 and wrong.json()["schemas"] == ["urn:ietf:params:scim:api:messages:2.0:Error"]
    token_id = client.get("/api/directory/scim").json()["tokens"][0]["id"]
    assert scim.get("/scim/v2/Users").status_code == 200
    assert client.delete(f"/api/directory/scim/tokens/{token_id}").status_code == 204
    assert scim.get("/scim/v2/Users").status_code == 401


def test_tokens_are_shown_once_and_stored_hashed(client, scim):
    setup = client.get("/api/directory/scim").json()
    assert setup["base_url"] == "http://localhost:8000/scim/v2"
    assert "token" not in setup["tokens"][0] and setup["tokens"][0]["last_used_at"] is None
    scim.get("/scim/v2/Users")
    assert client.get("/api/directory/scim").json()["tokens"][0]["last_used_at"] is not None


def test_only_admins_manage_tokens_and_mappings(viewer_client):
    assert viewer_client.post("/api/directory/scim/tokens", json={"label": "x"}).status_code == 403
    assert viewer_client.get("/api/directory/group-mappings").status_code == 403


def test_discovery_documents(scim):
    config = scim.get("/scim/v2/ServiceProviderConfig").json()
    assert config["patch"]["supported"] is True and config["bulk"]["supported"] is False
    assert {r["id"] for r in scim.get("/scim/v2/ResourceTypes").json()["Resources"]} == {"User", "Group"}
    assert scim.get("/scim/v2/Schemas/urn:ietf:params:scim:schemas:core:2.0:User").json()["name"] == "User"
    assert scim.get("/scim/v2/Schemas/urn:nope").status_code == 404


def test_unsupported_filter_and_bad_body_are_scim_errors(scim):
    bad = scim.get("/scim/v2/Users", params={"filter": 'userName co "x"'})
    assert bad.status_code == 400 and bad.json()["scimType"] == "invalidFilter"
    missing = scim.post("/scim/v2/Users", content=json.dumps({"schemas": [], "active": True}))
    assert missing.status_code == 400 and missing.json()["scimType"] == "invalidValue"
    not_json = scim.post("/scim/v2/Users", content="{oops")
    assert not_json.status_code == 400 and not_json.headers["content-type"].startswith(SCIM_JSON)


def _create_user(scim, user_name: str, external_id: str | None = None, **extra) -> dict:
    body = {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"], "userName": user_name, "active": True,
            "name": {"givenName": user_name.split("@")[0].title(), "familyName": "Tester"},
            "emails": [{"primary": True, "type": "work", "value": user_name}], **extra}
    if external_id:
        body["externalId"] = external_id
    response = scim.post("/scim/v2/Users", content=json.dumps(body))
    assert response.status_code == 201, response.text
    return response.json()


def _patch(scim, path: str, *operations) -> int:
    body = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": list(operations)}
    return scim.patch(f"/scim/v2{path}", content=json.dumps(body)).status_code


# ---- US37.10: joiners and leavers --------------------------------------------------------


def test_deactivation_revokes_every_session_at_once(client, scim, monkeypatch):
    user = _create_user(scim, "leaver@example.com", "ext-leaver")
    # The user signs in (locally here; over SSO in test_oidc) -- two browsers.
    with get_session_factory()() as db:
        from app.auth import sessions
        from app.db.models import UserModel

        record = db.get(UserModel, user["id"])
        for _ in range(2):
            sessions.start(db, record)
        db.commit()
    assert _patch(scim, f"/Users/{user['id']}", {"op": "Replace", "path": "active", "value": "False"}) == 200
    with get_session_factory()() as db:
        live = [s for s in db.scalars(select(SessionModel).where(SessionModel.user_id == user["id"])) if s.revoked_at is None]
        event = db.scalar(select(AuditEventModel).where(AuditEventModel.action == "user_deactivated"))
    assert live == []
    assert (event.actor_type, event.actor_name, event.detail["sessions_revoked"]) == ("service", "SCIM (Entra ID)", 2)
    users = {u["name"]: u for u in client.get("/api/users").json()}
    assert users["Leaver Tester"]["active"] is False and users["Leaver Tester"]["provisioned_by"] == "scim"


def test_deleted_user_keeps_history_and_frees_the_user_name(client, scim):
    first = _create_user(scim, "dana@example.com")
    assert scim.delete(f"/scim/v2/Users/{first['id']}").status_code == 204
    assert scim.get(f"/scim/v2/Users/{first['id']}").status_code == 404
    assert scim.get("/scim/v2/Users", params={"filter": 'userName eq "dana@example.com"'}).json()["totalResults"] == 0
    again = _create_user(scim, "dana@example.com")  # a rehire
    assert again["id"] != first["id"]
    kept = {u["id"]: u for u in client.get("/api/users").json()}
    assert kept[first["id"]]["active"] is False


def test_user_name_is_unique_ignoring_case(scim):
    _create_user(scim, "Pat@Example.com")
    duplicate = scim.post("/scim/v2/Users", content=json.dumps({"schemas": [], "userName": "pat@example.com"}))
    assert duplicate.status_code == 409


# ---- US37.11: groups to roles ---------------------------------------------------------------


def _map(client, *mappings) -> dict:
    response = client.put("/api/directory/group-mappings", json={"mappings": [{"group": g, "role": r} for g, r in mappings]})
    assert response.status_code == 200, response.text
    return response.json()


def _roles(client, user_id: str) -> set[tuple[str, str]]:
    user = next(u for u in client.get("/api/users").json() if u["id"] == user_id)
    return {(g["role"], g["source"]) for g in user["roles"]}


def test_group_membership_grants_and_removes_roles(client, scim):
    _map(client, ("Finance Approvers", "approver"), ("finance-approvers-object-id", "auditor"))
    user = _create_user(scim, "fin@example.com")
    group = scim.post("/scim/v2/Groups", content=json.dumps({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"], "displayName": "finance approvers",  # case differs
        "externalId": "finance-approvers-object-id", "members": [{"value": user["id"]}],
    })).json()
    assert _roles(client, user["id"]) == {("approver", "group"), ("auditor", "group")}
    assert _patch(scim, f"/Groups/{group['id']}", {"op": "remove", "path": f'members[value eq "{user["id"]}"]'}) == 204
    assert _roles(client, user["id"]) == set()
    with get_session_factory()() as db:
        changes = list(db.scalars(select(AuditEventModel).where(AuditEventModel.action == "roles_changed")))
    assert [c.detail["lost"] for c in changes][-1] == ["approver@organization", "auditor@organization"]


def test_renaming_a_group_reapplies_mappings(client, scim):
    _map(client, ("Approvers", "approver"))
    user = _create_user(scim, "ren@example.com")
    group = scim.post("/scim/v2/Groups", content=json.dumps({
        "schemas": [], "displayName": "Approvers", "members": [{"value": user["id"]}]})).json()
    assert _roles(client, user["id"]) == {("approver", "group")}
    _patch(scim, f"/Groups/{group['id']}", {"op": "Replace", "path": "displayName", "value": "Former approvers"})
    assert _roles(client, user["id"]) == set()


def test_deleting_a_group_removes_its_roles(client, scim):
    _map(client, ("Auditors", "auditor"))
    user = _create_user(scim, "aud@example.com")
    group = scim.post("/scim/v2/Groups", content=json.dumps({
        "schemas": [], "displayName": "Auditors", "members": [{"value": user["id"]}]})).json()
    assert scim.delete(f"/scim/v2/Groups/{group['id']}").status_code == 204
    assert _roles(client, user["id"]) == set()


def test_unknown_members_are_refused(scim):
    response = scim.post("/scim/v2/Groups", content=json.dumps({
        "schemas": [], "displayName": "G", "members": [{"value": "user_nobody"}]}))
    assert response.status_code == 400 and response.json()["scimType"] == "invalidValue"


def test_mapping_preview_changes_nothing_and_save_applies(client, scim):
    _map(client, ("Approvers", "approver"))
    user = _create_user(scim, "prev@example.com")
    scim.post("/scim/v2/Groups", content=json.dumps({"schemas": [], "displayName": "Approvers", "members": [{"value": user["id"]}]}))
    proposed = {"mappings": [{"group": "Approvers", "role": "auditor"}]}
    preview = client.post("/api/directory/group-mappings/preview", json=proposed).json()
    assert preview == [{"user_id": user["id"], "user_name": "Prev Tester",
                        "gained": [{"role": "auditor", "scope_type": "organization", "scope_id": "", "scope_name": None}],
                        "lost": [{"role": "approver", "scope_type": "organization", "scope_id": "", "scope_name": None}]}]
    assert _roles(client, user["id"]) == {("approver", "group")}  # unchanged
    saved = client.put("/api/directory/group-mappings", json=proposed).json()
    assert len(saved["changes"]) == 1
    assert _roles(client, user["id"]) == {("auditor", "group")}


def test_mappings_are_validated(client):
    bad_role = client.put("/api/directory/group-mappings", json={"mappings": [{"group": "G", "role": "wizard"}]})
    assert bad_role.status_code == 422
    bad_unit = client.put("/api/directory/group-mappings", json={"mappings": [
        {"group": "G", "role": "analyst", "scope_type": "business_unit", "scope_id": "nope"}]})
    assert bad_unit.status_code == 400 and "business unit" in bad_unit.json()["detail"]


def test_business_unit_scoped_mapping(client, scim):
    unit = client.post("/api/organization/entities", json={"kind": "business_unit", "name": "Retail"}).json()
    client.put("/api/directory/group-mappings", json={"mappings": [
        {"group": "Retail Analysts", "role": "analyst", "scope_type": "business_unit", "scope_id": unit["id"]}]})
    user = _create_user(scim, "retail@example.com")
    scim.post("/scim/v2/Groups", content=json.dumps({"schemas": [], "displayName": "Retail Analysts",
                                                     "members": [{"value": user["id"]}]}))
    roles = next(u for u in client.get("/api/users").json() if u["id"] == user["id"])["roles"]
    assert [(g["role"], g["scope_id"], g["scope_name"]) for g in roles] == [("analyst", unit["id"], "Retail")]


def test_known_groups_list_scim_groups(client, scim):
    user = _create_user(scim, "kg@example.com")
    scim.post("/scim/v2/Groups", content=json.dumps({"schemas": [], "displayName": "Ops", "externalId": "ops-1",
                                                     "members": [{"value": user["id"]}]}))
    assert client.get("/api/directory/groups").json() == [
        {"name": "Ops", "external_id": "ops-1", "source": "scim", "members": 1}
    ]


def test_admin_cannot_hand_edit_group_roles_away(client, scim):
    _map(client, ("Approvers", "approver"))
    user = _create_user(scim, "keep@example.com")
    scim.post("/scim/v2/Groups", content=json.dumps({"schemas": [], "displayName": "Approvers", "members": [{"value": user["id"]}]}))
    client.put(f"/api/users/{user['id']}/roles", json={"roles": []})
    assert _roles(client, user["id"]) == {("approver", "group")}


def test_local_sign_in_is_unaffected(client):
    c = TestClient(app)
    assert sign_in(c, "local-person", ["analyst"]).status_code == 200
