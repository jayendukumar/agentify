"""Epic 22: per-run execution snapshots (US22.1/US22.2) and the three
fixed reproducibility-tier labels (US22.3). The snapshot is recorded at
run *creation*, before any execution -- these tests only need the create
call to succeed, not the run to reach a particular terminal state, but the
background task still runs synchronously within TestClient, so `fake_llm`
is stubbed just enough that schema inference doesn't crash."""

import json

from app.llm.types import ChatCompletionResult, Usage

from .test_api_twin import _make_artifact, _scenario_payload


def _stub_schema_inference(fake_llm) -> None:
    schema_payload = {
        "tools": [
            {
                "system_name": "CRM API",
                "tool_name": "lookup_record",
                "description": "Look up a record by id.",
                "parameters": {"type": "object", "properties": {"record_id": {"type": "string"}}, "required": ["record_id"]},
                "response_shape_description": "An object with a 'tier' field.",
            }
        ]
    }
    fake_llm.complete.return_value = ChatCompletionResult(
        text=json.dumps(schema_payload), tool_calls=[], finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model",
    )


def test_snapshot_recorded_at_run_creation(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    _stub_schema_inference(fake_llm)
    run = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run").json()

    view = client.get(f"/api/processes/{process['id']}/runs/{run['id']}/snapshot").json()
    assert view["run_id"] == run["id"]
    snapshot = view["snapshot"]
    assert snapshot is not None
    assert snapshot["kind"] == "twin"
    assert snapshot["scenario_id"] == scenario["id"]
    assert len(snapshot["agent_artifacts"]) == 1
    assert snapshot["agent_artifacts"][0]["agent_artifact_id"] == artifact["id"]
    assert snapshot["agent_artifacts"][0]["agent_version"] == 1
    assert snapshot["twin_environment"] is None


def test_snapshot_reflects_agent_version_bump(client, fake_llm):
    """US19.5's version bump is exactly what US22.1's snapshot is supposed
    to key against -- confirm a regenerated artifact's next run snapshots
    the new version, not the version at scenario-creation time."""
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()
    client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact")  # regenerate -> version 2

    _stub_schema_inference(fake_llm)
    run = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run").json()
    view = client.get(f"/api/processes/{process['id']}/runs/{run['id']}/snapshot").json()
    assert view["snapshot"]["agent_artifacts"][0]["agent_version"] == 2


def test_snapshot_includes_twin_environment(client, fake_llm):
    process, artifact = _make_artifact(client)
    env = client.post(
        f"/api/processes/{process['id']}/twin-environments",
        json={"name": "Env", "systems": ["CRM API"], "datasets": {}, "personas": {}, "events": [], "faults": {}},
    ).json()
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios",
        json=_scenario_payload(environment_id=env["id"]),
    ).json()

    _stub_schema_inference(fake_llm)
    run = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run").json()
    view = client.get(f"/api/processes/{process['id']}/runs/{run['id']}/snapshot").json()
    assert view["snapshot"]["twin_environment"]["twin_environment_id"] == env["id"]
    assert view["snapshot"]["twin_environment"]["twin_environment_version"] == 1


def test_snapshot_reproducibility_tiers_are_fixed(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()
    _stub_schema_inference(fake_llm)
    run = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run").json()

    view = client.get(f"/api/processes/{process['id']}/runs/{run['id']}/snapshot").json()
    assert view["reproducibility"] == {
        "configuration": "guaranteed",
        "behavioral": "best_effort",
        "exact_output": "not_guaranteed",
    }
    assert set(view["explanation"]) == {"configuration", "behavioral", "exact_output"}


def test_snapshot_for_unknown_run_404s(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.get(f"/api/processes/{process['id']}/runs/simrun_nonexistent/snapshot")
    assert r.status_code == 404
