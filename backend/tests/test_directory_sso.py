"""Epic 37, US37.10-37.11 where SCIM provisioning meets SSO sign-in:
linking a provisioned user to their first sign-in, whose groups count,
and mappings applied to people who signed in with a groups claim."""

import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import UserIdentityModel, UserModel
from app.db.session import get_session_factory
from app.main import app

from .test_oidc import _error, _sso, idp  # noqa: F401  (idp is a fixture)

SCIM_JSON = "application/scim+json"


def _scim(client) -> TestClient:
    token = client.post("/api/directory/scim/tokens", json={"label": "Okta"}).json()["token"]
    return TestClient(app, headers={"Authorization": f"Bearer {token}", "Content-Type": SCIM_JSON})


def _provision(scim, user_name: str, external_id: str, groups: list[str] = ()) -> str:
    user = scim.post("/scim/v2/Users", content=json.dumps({
        "schemas": [], "userName": user_name, "externalId": external_id, "active": True,
        "name": {"givenName": "Olivia", "familyName": "Brandt"},
    })).json()
    for name in groups:
        scim.post("/scim/v2/Groups", content=json.dumps({"schemas": [], "displayName": name, "members": [{"value": user["id"]}]}))
    return user["id"]


def test_provisioned_user_is_linked_at_first_sign_in(client, idp, monkeypatch):
    monkeypatch.setenv("OIDC_JIT_PROVISIONING", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    scim = _scim(client)
    user_id = _provision(scim, "olivia@meridian.example", "sub-olivia", ["axyntro-approvers"])
    browser, done = _sso(idp, sub="sub-olivia", email="olivia@meridian.example", groups=["axyntro-admins"])
    assert _error(done) is None
    me = browser.get("/api/auth/me").json()
    assert me["id"] == user_id and me["provisioned_by"] == "scim"
    # SCIM owns a provisioned user's groups: the claim's admin group is ignored.
    assert [(g["role"], g["source"]) for g in me["roles"]] == [("approver", "group")]
    with get_session_factory()() as db:
        assert db.get(UserIdentityModel, ("https://idp.test/realms/axyntro", "sub-olivia")).user_id == user_id


def test_link_uses_the_immutable_id_not_the_email(client, idp, monkeypatch):
    monkeypatch.setenv("OIDC_JIT_PROVISIONING", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    _provision(_scim(client), "olivia@meridian.example", "sub-olivia")
    # Same email, different IdP account: not linked, and JIT is off.
    _, done = _sso(idp, sub="sub-impostor", email="olivia@meridian.example")
    assert _error(done) == "not_provisioned"


def test_deprovisioned_user_cannot_sign_in(client, idp):
    scim = _scim(client)
    user_id = _provision(scim, "gone@meridian.example", "sub-gone")
    browser, _ = _sso(idp, sub="sub-gone")
    assert browser.get("/api/auth/me").status_code == 200
    body = {"schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"], "Operations": [{"op": "replace", "value": {"active": False}}]}
    assert scim.patch(f"/scim/v2/Users/{user_id}", content=json.dumps(body)).status_code == 200
    assert browser.get("/api/auth/me").status_code == 401  # the next request, not the next refresh
    _, done = _sso(idp, sub="sub-gone")
    assert _error(done) == "deactivated"


def test_sso_user_provisioned_later_is_adopted(client, idp):
    browser, _ = _sso(idp, sub="sub-early", email="early@meridian.example", name="Early Bird", groups=[])
    early_id = browser.get("/api/auth/me").json()["id"]
    adopted = _scim(client).post("/scim/v2/Users", content=json.dumps({
        "schemas": [], "userName": "early@meridian.example", "externalId": "sub-early", "active": True})).json()
    assert adopted["id"] == early_id
    with get_session_factory()() as db:
        assert db.scalar(select(UserModel.provisioned_by).where(UserModel.id == early_id)) == "scim"


def test_mapping_change_reaches_claim_users_without_a_new_sign_in(client, idp):
    browser, _ = _sso(idp, sub="sub-claims", groups=["finance-team"])
    user_id = browser.get("/api/auth/me").json()["id"]
    assert browser.get("/api/auth/me").json()["roles"] == []
    groups = {g["name"]: g for g in client.get("/api/directory/groups").json()}
    assert groups["finance-team"]["source"] == "sign-in"

    proposal = {"mappings": [*client.get("/api/directory/group-mappings").json(), {"group": "Finance-Team", "role": "auditor"}]}
    preview = client.post("/api/directory/group-mappings/preview", json=proposal).json()
    assert [(c["user_id"], [g["role"] for g in c["gained"]]) for c in preview] == [(user_id, ["auditor"])]
    client.put("/api/directory/group-mappings", json=proposal)
    assert [g["role"] for g in browser.get("/api/auth/me").json()["roles"]] == ["auditor"]


def test_users_whose_groups_were_never_recorded_are_left_alone(client, idp):
    """Found live: people who last signed in before groups were recorded
    showed up in the preview as losing every role."""
    browser, _ = _sso(idp, sub="sub-before-upgrade", groups=["axyntro-approvers"])
    user_id = browser.get("/api/auth/me").json()["id"]
    with get_session_factory()() as db:
        db.get(UserModel, user_id).idp_groups = None  # as after the migration
        db.commit()
    proposal = {"mappings": [*client.get("/api/directory/group-mappings").json(), {"group": "other", "role": "auditor"}]}
    assert client.post("/api/directory/group-mappings/preview", json=proposal).json() == []
    client.put("/api/directory/group-mappings", json=proposal)
    assert [g["role"] for g in browser.get("/api/auth/me").json()["roles"]] == ["approver"]


def test_bootstrap_mappings_seed_once(client, idp):
    _sso(idp, groups=["axyntro-approvers"])  # first use seeds from OIDC_GROUP_ROLES
    seeded = client.get("/api/directory/group-mappings").json()
    assert {"group": "axyntro-approvers", "role": "approver"}.items() <= next(
        m for m in seeded if m["group"] == "axyntro-approvers").items()
    assert client.put("/api/directory/group-mappings", json={"mappings": []}).status_code == 200
    browser, _ = _sso(idp, groups=["axyntro-approvers"])
    assert client.get("/api/directory/group-mappings").json() == []  # the admin's choice stands
    assert browser.get("/api/auth/me").json()["roles"] == []
