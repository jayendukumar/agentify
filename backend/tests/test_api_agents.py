from app.db import repository
from app.db.session import get_session_factory
from app.schemas.blueprint import AgentIOField, AgentSpec, BlueprintNodeResult

from .test_api_blueprint import _finalize


def _seed_blueprint(process_id: str, version_id: str, nodes: list[BlueprintNodeResult]) -> None:
    session = get_session_factory()()
    try:
        repository.set_blueprint_overlay(session, process_id, version_id, nodes)
        session.commit()
    finally:
        session.close()


def _automatable_node(node_id: str, **spec_overrides) -> BlueprintNodeResult:
    spec = AgentSpec(
        name=f"{node_id} Agent",
        purpose="Look up and reformat a record",
        trigger="Upstream step completes",
        required_inputs=[AgentIOField(name="record_id", source_or_destination="CRM", format="string")],
        expected_outputs=[AgentIOField(name="summary", source_or_destination="ticketing system", format="text")],
        tools_systems_needed=["CRM API"],
        human_checkpoint="review_after_action",
        **spec_overrides,
    )
    return BlueprintNodeResult(
        node_id=node_id,
        verdict="automatable",
        step_type="data_retrieval_transformation",
        rationale=f"{node_id} is a mechanical lookup with no judgment required.",
        agent_spec=spec,
    )


def _not_automatable_node(node_id: str) -> BlueprintNodeResult:
    return BlueprintNodeResult(
        node_id=node_id,
        verdict="not_automatable",
        step_type="approval_compliance_signoff",
        rationale="Requires accountable sign-off.",
        not_automatable_reason="Compliance requires a specific accountable human.",
    )


def test_generate_agent_artifact_from_automatable_node(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a"), _not_automatable_node("b")])

    r = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    assert r.status_code == 200
    body = r.json()
    assert body["primary_node_id"] == "a"
    assert body["node_ids"] == ["a"]
    assert body["status"] == "generated"
    assert body["baseline_version_id"] == version["id"]
    definition = body["definition"]
    assert definition["name"] == "a Agent"
    assert definition["model"]
    assert "a Agent" in definition["system_prompt"]
    assert "CRM API" in definition["system_prompt"]
    assert definition["input_schema"][0]["name"] == "record_id"
    assert definition["output_schema"][0]["name"] == "summary"


def test_generate_agent_artifact_on_not_automatable_node_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a"), _not_automatable_node("b")])

    r = client.post(f"/api/processes/{process['id']}/blueprint/nodes/b/agent-artifact")
    assert r.status_code == 400


def test_generate_agent_artifact_unknown_node_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])

    r = client.post(f"/api/processes/{process['id']}/blueprint/nodes/does-not-exist/agent-artifact")
    assert r.status_code == 400


def test_generate_agent_artifact_without_blueprint_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])

    r = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    assert r.status_code == 404


def test_consolidated_group_generates_one_shared_artifact(client):
    # US12.5 -- two nodes both listing the same consolidated_from_nodes
    # group resolve to the SAME artifact regardless of which one is
    # clicked, keyed by group_key not by whichever node triggered it.
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    group_node_a = _automatable_node("a", consolidated_from_nodes=["a", "b"])
    group_node_b = _automatable_node("b", consolidated_from_nodes=["a", "b"])
    _seed_blueprint(process["id"], version["id"], [group_node_a, group_node_b])

    r1 = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    assert r1.status_code == 200
    artifact_a = r1.json()
    assert sorted(artifact_a["node_ids"]) == ["a", "b"]

    r2 = client.post(f"/api/processes/{process['id']}/blueprint/nodes/b/agent-artifact")
    assert r2.status_code == 200
    artifact_b = r2.json()

    assert artifact_a["id"] == artifact_b["id"]
    assert artifact_b["primary_node_id"] == "b"

    listed = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert len(listed) == 1


def test_list_agent_artifacts_marks_stale_after_override(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")

    listed = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert listed[0]["status"] == "generated"

    client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/a",
        json={"verdict": "partial", "justification": "Actually needs a human check first"},
    )

    listed_after = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert listed_after[0]["status"] == "stale"


def test_list_agent_artifacts_marks_stale_after_blueprint_regenerate(client, fake_llm):
    # A real /generate call (unlike the direct-seed helper above) validates
    # full node coverage against the finalized version's actual BPMN XML
    # (Event_start, Task_a, Event_end -- see test_api_blueprint.py's
    # _valid_xml), so this test's LLM responses must cover all three.
    from .test_api_blueprint import _all_nodes_response, _llm_json

    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(_all_nodes_response())
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact")

    # A fresh regenerate replaces the whole overlay with a new LLM response
    # -- even with the same verdict, the stored rationale/spec differs, so
    # the artifact's snapshot no longer matches current state.
    second_response = _all_nodes_response()
    second_response["nodes"][1]["rationale"] = "Re-evaluated after a baseline change."
    fake_llm.complete.return_value = _llm_json(second_response)
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})

    listed = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert listed[0]["status"] == "stale"


def test_regenerate_artifact_clears_stale_status(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/a",
        json={"verdict": "partial", "justification": "Needs a human check"},
    )

    r = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    assert r.status_code == 200
    assert r.json()["status"] == "generated"

    listed = client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts").json()
    assert listed[0]["status"] == "generated"


def test_list_agent_artifacts_requires_login(client, viewer_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])

    # Viewer can read...
    r = viewer_client.get(f"/api/processes/{process['id']}/blueprint/agent-artifacts")
    assert r.status_code == 200

    # ...but not generate.
    r = viewer_client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")
    assert r.status_code == 403
