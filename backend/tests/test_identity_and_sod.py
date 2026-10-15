"""Epic 37: roles and scopes (US37.1-37.2), contribution history and
segregation of duties (US37.3-37.5), the audit log (US37.6), and the
production guard on local sign-in (US37.14)."""

import pytest
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app

from .test_api_agents import _automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize, _valid_xml, approve_agent_as_other_user
from .conftest import sign_in


def _login(name: str, roles: list[str], business_unit_id: str | None = None) -> TestClient:
    c = TestClient(app)
    r = sign_in(c, name, roles, business_unit_id)
    assert r.status_code == 200, r.text
    return c


def _agent(client) -> tuple[str, dict]:
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact").json()
    return process["id"], artifact


def _url(process_id: str, artifact_id: str, tail: str = "") -> str:
    return f"/api/processes/{process_id}/blueprint/agent-artifacts/{artifact_id}{tail}"


# ---- US37.2: every write is guarded ----------------------------------------------


def test_every_write_route_declares_a_permission():
    # iter_route_contexts, not app.routes: FastAPI 0.141 wraps each included
    # router, so app.routes alone holds only the few routes declared on the
    # app itself -- and this test passed while checking none of them.
    unguarded, checked = [], 0
    for route in iter_route_contexts(app.routes):
        if not isinstance(route.original_route, APIRoute) or not route.path.startswith("/api"):
            continue
        if not (route.methods - {"GET", "HEAD", "OPTIONS"}) or route.path.startswith("/api/auth/"):
            continue
        checked += 1
        calls = []
        stack = list(route.dependant.dependencies)
        while stack:
            dep = stack.pop()
            calls.append(dep.call)
            stack.extend(dep.dependencies)
        if not any(getattr(call, "required_permission", None) for call in calls):
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert checked > 100, f"only {checked} write routes found -- is the route walk still seeing every router?"
    assert unguarded == []


# ---- US37.1/US37.2: scoped roles --------------------------------------------------


def test_business_unit_role_covers_only_that_unit(client):
    banking = client.post("/api/organization/entities", json={"kind": "business_unit", "name": "Banking"}).json()
    insurance = client.post("/api/organization/entities", json={"kind": "business_unit", "name": "Insurance"}).json()
    ours = client.post("/api/processes", json={"name": "Ours", "scope": {"business_unit_id": banking["id"]}}).json()
    theirs = client.post("/api/processes", json={"name": "Theirs", "scope": {"business_unit_id": insurance["id"]}}).json()

    analyst = _login("banking-analyst", ["analyst"], banking["id"])
    xml = {"xml": _valid_xml(ours["id"])}
    assert analyst.put(f"/api/processes/{ours['id']}/bpmn", json=xml).status_code == 200
    refused = analyst.put(f"/api/processes/{theirs['id']}/bpmn", json={"xml": _valid_xml(theirs["id"])})
    assert refused.status_code == 403
    assert "business unit" in refused.json()["detail"]

    # Creating or moving a process stays within the role's scope.
    assert analyst.post("/api/processes", json={"name": "Org-wide"}).status_code == 403
    assert analyst.post(
        "/api/processes", json={"name": "In unit", "scope": {"business_unit_id": banking["id"]}}
    ).status_code == 201
    assert analyst.put(f"/api/processes/{ours['id']}/scope", json={"business_unit_id": insurance["id"]}).status_code == 403


def test_admin_cannot_build_or_approve(client):
    process_id, artifact = _agent(client)
    admin = _login("platform-admin", ["admin"])
    assert admin.post(f"/api/processes/{process_id}/blueprint/nodes/a/agent-artifact").status_code == 403
    assert admin.post(_url(process_id, artifact["id"], "/approve")).status_code == 403
    assert admin.get("/api/users").status_code == 200


def test_admin_manages_roles_and_last_admin_is_protected(client):
    admin = _login("only-admin", ["admin"])
    target = _login("someone", [])
    target_id = target.get("/api/auth/me").json()["id"]
    updated = admin.put(f"/api/users/{target_id}/roles", json={"roles": [{"role": "auditor"}]})
    assert updated.status_code == 200
    assert [g["role"] for g in updated.json()["roles"]] == ["auditor"]
    assert target.get("/api/audit-events").status_code == 200

    # `client` also holds admin, so remove it there first.
    client_id = client.get("/api/auth/me").json()["id"]
    assert admin.put(f"/api/users/{client_id}/roles", json={"roles": [{"role": "analyst"}]}).status_code == 200
    admin_id = admin.get("/api/auth/me").json()["id"]
    assert admin.put(f"/api/users/{admin_id}/roles", json={"roles": []}).status_code == 409


def test_deactivated_user_loses_sessions(client):
    victim = _login("leaver", ["analyst"])
    victim_id = victim.get("/api/auth/me").json()["id"]
    assert client.post(f"/api/users/{victim_id}/deactivate").status_code == 200
    assert victim.get("/api/auth/me").status_code == 401
    assert TestClient(app).post("/api/auth/login", json={"name": "leaver", "roles": []}).status_code == 403


# ---- US37.4: baselines -------------------------------------------------------------


def test_finalized_baseline_waits_for_an_independent_approval(client, approver_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    pid = process["id"]
    version = _finalize(client, pid, approve=False)
    assert version["status"] == "pending_approval"

    # Not the baseline yet: the blueprint has nothing approved to evaluate.
    r = client.post(f"/api/processes/{pid}/blueprint/generate", json={})
    assert r.status_code == 400 and "approved baseline" in r.json()["detail"]

    # The person who edited the diagram can't approve it, even holding the role.
    status = client.get(f"/api/processes/{pid}/versions/{version['id']}/approval").json()
    assert status["can_approve"] is False and "edited the diagram" in status["reason"]
    assert status["changes"][0]["tier"] == "high"
    assert client.post(f"/api/processes/{pid}/versions/{version['id']}/approve", json={}).status_code == 403

    approved = approver_client.post(f"/api/processes/{pid}/versions/{version['id']}/approve", json={})
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_by_name"] == "test-approver"


def test_editing_the_draft_supersedes_a_pending_baseline(client, approver_client):
    pid = client.post("/api/processes", json={"name": "P"}).json()["id"]
    version = _finalize(client, pid, approve=False)
    client.put(f"/api/processes/{pid}/bpmn", json={"xml": _valid_xml(pid)})
    versions = client.get(f"/api/processes/{pid}/versions").json()
    assert versions[0]["status"] == "superseded"
    assert approver_client.post(f"/api/processes/{pid}/versions/{version['id']}/approve", json={}).status_code == 409


def test_rejected_baseline_is_not_used(client, approver_client):
    pid = client.post("/api/processes", json={"name": "P"}).json()["id"]
    version = _finalize(client, pid, approve=False)
    r = approver_client.post(f"/api/processes/{pid}/versions/{version['id']}/reject", json={"comment": "Missing step"})
    assert r.json()["status"] == "rejected"
    assert client.post(f"/api/processes/{pid}/blueprint/generate", json={"version_id": version["id"]}).status_code == 400


# ---- US37.4/US37.5: agents ---------------------------------------------------------


def test_any_definition_edit_needs_a_fresh_approval(client):
    process_id, artifact = _agent(client)
    approved = approve_agent_as_other_user(process_id, artifact["id"])
    assert approved["lifecycle_status"] == "APPROVED"

    # A small governance edit by the builder voids the approval...
    edited = client.patch(
        _url(process_id, artifact["id"], "/governance"), json={"tools_systems_needed": ["CRM API", "Email"]}
    ).json()
    assert edited["approved_at"] is None
    assert edited["lifecycle_status"] != "APPROVED"
    # ...and the new change set lists who made it.
    status = client.get(_url(process_id, artifact["id"], "/approval")).json()
    assert status["can_approve"] is False
    assert "edited the agent definition" in status["reason"]
    assert approve_agent_as_other_user(process_id, artifact["id"])["lifecycle_status"] == "APPROVED"


def test_standard_contributions_do_not_disqualify_an_approver(client):
    process_id, artifact = _agent(client)
    reviewer = _login("tester-approver", ["architect", "approver"])
    assert reviewer.post(_url(process_id, artifact["id"], "/mark-release-candidate")).status_code == 200
    status = reviewer.get(_url(process_id, artifact["id"], "/approval")).json()
    assert {c["tier"] for c in status["changes"]} == {"high", "standard"}
    assert status["can_approve"] is True
    assert reviewer.post(_url(process_id, artifact["id"], "/approve")).json()["lifecycle_status"] == "APPROVED"


def test_two_person_approval(client):
    assert client.put("/api/organization/governance", json={"required_agent_approvals": 2}).status_code == 200
    process_id, artifact = _agent(client)
    first = _login("approver-one", ["approver"])
    after_one = first.post(_url(process_id, artifact["id"], "/approve")).json()
    assert after_one["approved_at"] is None
    assert first.post(_url(process_id, artifact["id"], "/approve")).status_code == 409  # same person twice
    after_two = _login("approver-two", ["approver"]).post(_url(process_id, artifact["id"], "/approve")).json()
    assert after_two["lifecycle_status"] == "APPROVED"
    status = client.get(_url(process_id, artifact["id"], "/approval")).json()
    assert [a["approver_name"] for a in status["approvals"]] == ["approver-one", "approver-two"]


def test_publish_needs_approval_by_someone_else(client):
    process_id, artifact = _agent(client)
    publish = f"/api/processes/{process_id}/agent-artifacts/{artifact['id']}/publish"
    assert client.post(publish, json={"registry_name": "local"}).status_code == 409  # not approved yet

    both = _login("approver-publisher", ["approver", "architect"])
    both.post(_url(process_id, artifact["id"], "/approve"))
    refused = both.post(publish, json={"registry_name": "local"})
    assert refused.status_code == 403 and "someone else must publish" in refused.json()["detail"]
    assert client.post(publish, json={"registry_name": "local"}).status_code == 200


# ---- US37.6: audit log -------------------------------------------------------------


def test_audit_log_records_writes_and_refusals(client, viewer_client):
    process_id, artifact = _agent(client)
    viewer_client.post("/api/processes", json={"name": "Nope"})
    client.post(_url(process_id, artifact["id"], "/approve"))  # SoD refusal

    events = client.get("/api/audit-events").json()
    actions = {(e["action"], e["outcome"]) for e in events}
    assert ("sign_in", "success") in actions
    assert ("generate_agent_artifact", "success") in actions
    assert ("create_process", "refused") in actions
    assert ("approve_agent_artifact", "refused") in actions
    refusal = next(e for e in events if e["action"] == "approve_agent_artifact" and e["outcome"] == "refused")
    assert "someone else must approve" in refusal["detail"]["reason"]

    assert viewer_client.get("/api/audit-events").status_code == 403
    csv = client.get("/api/audit-events/export?outcome=refused")
    assert csv.status_code == 200 and "approve_agent_artifact" in csv.text


# ---- US37.14: local sign-in is refused in production -------------------------------


def _settings(**overrides) -> Settings:
    return Settings(openrouter_api_key="k", **overrides)


def test_local_sign_in_is_refused_in_production():
    with pytest.raises(ValidationError, match="AUTH_MODE=local is not allowed with ENVIRONMENT=production"):
        _settings(environment="production", auth_mode="local")


def test_saml_is_refused_until_built():
    with pytest.raises(ValidationError, match="not available in this build"):
        _settings(auth_mode="saml")


def test_oidc_without_idp_configuration_is_refused(monkeypatch):
    monkeypatch.delenv("OIDC_CLIENT_SECRET", raising=False)
    with pytest.raises(ValidationError, match="OIDC_ISSUER, OIDC_CLIENT_ID, OIDC_CLIENT_SECRET"):
        _settings(auth_mode="oidc")


def test_oidc_with_idp_configuration_runs_in_production(monkeypatch):
    monkeypatch.setenv("OIDC_CLIENT_SECRET", "s3cret")
    settings = _settings(
        environment="production", auth_mode="oidc", oidc_issuer="https://idp.example/realms/x", oidc_client_id="axyntro"
    )
    assert settings.oidc_client_secret == "s3cret"


def test_unknown_environment_is_refused():
    with pytest.raises(ValidationError):
        _settings(environment="staging")


def test_health_reports_auth_mode():
    assert TestClient(app).get("/healthz").json()["auth_mode"] == "local"
