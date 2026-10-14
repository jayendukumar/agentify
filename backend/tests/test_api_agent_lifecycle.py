"""Epic 23: the validation checklist (US23.2), the computed lifecycle
status (US23.1), the SIMULATION_READY precondition on starting a run
(US23.3), and the mark-release-candidate/approve actions (US23.4)."""

from .test_api_agents import _automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize, approve_agent_as_other_user
from .test_api_twin import _scenario_payload


def _agent_url(process_id: str, suffix: str = "") -> str:
    return f"/api/processes/{process_id}/blueprint{suffix}"


def test_freshly_generated_artifact_checklist_is_complete_except_test_scenarios(client):
    """generation.py's real defaults (Epic 20/23) mean a freshly generated
    artifact never has to wait in DRAFT for a human to fill anything in --
    except for test_scenarios, which genuinely needs at least one scenario
    to exist."""
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])

    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()
    unsatisfied = {item["key"] for item in artifact["checklist"] if not item["satisfied"]}
    assert unsatisfied == {"test_scenarios"}
    assert artifact["lifecycle_status"] == "DRAFT"
    assert {item["key"] for item in artifact["checklist"]} >= {"success_criteria", "completion_conditions"}


def test_artifact_becomes_simulation_ready_once_a_scenario_exists(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()

    client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    )

    listed = client.get(_agent_url(process["id"], "/agent-artifacts")).json()
    assert listed[0]["lifecycle_status"] == "SIMULATION_READY"


def test_checklist_marks_escalation_rules_unsatisfied_when_cleared(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()

    updated = client.patch(
        _agent_url(process["id"], f"/agent-artifacts/{artifact['id']}/governance"),
        json={"escalation_policy": {"escalate_when": [], "escalation_target": None}},
    ).json()
    assert updated["lifecycle_status"] == "DRAFT"
    escalation_item = next(item for item in updated["checklist"] if item["key"] == "escalation_rules")
    assert escalation_item["satisfied"] is False


def test_start_run_against_incomplete_checklist_is_rejected(client):
    """US23.3: refuses to start a twin run against an artifact that
    hasn't reached SIMULATION_READY."""
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    client.patch(
        _agent_url(process["id"], f"/agent-artifacts/{artifact['id']}/governance"),
        json={"escalation_policy": {"escalate_when": [], "escalation_target": None}},
    )

    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 400
    assert "SIMULATION_READY" in r.json()["detail"]


def test_mark_release_candidate_and_approve(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()

    marked = client.post(
        _agent_url(process["id"], f"/agent-artifacts/{artifact['id']}/mark-release-candidate")
    ).json()
    assert marked["lifecycle_status"] == "RELEASE_CANDIDATE"
    assert marked["marked_release_candidate_at"] is not None

    # Epic 37, US37.4: the builder can't approve their own agent.
    refused = client.post(_agent_url(process["id"], f"/agent-artifacts/{artifact['id']}/approve"))
    assert refused.status_code == 403
    assert "generated the agent definition" in refused.json()["detail"]

    approved = approve_agent_as_other_user(process["id"], artifact["id"])
    assert approved["lifecycle_status"] == "APPROVED"
    assert approved["approved_at"] is not None
    assert approved["approved_by"] is not None


def test_mark_release_candidate_requires_editor(client, viewer_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(_agent_url(process["id"], "/nodes/a/agent-artifact")).json()

    r = viewer_client.post(_agent_url(process["id"], f"/agent-artifacts/{artifact['id']}/mark-release-candidate"))
    assert r.status_code == 403
