import json

import pytest

from app.llm.types import ChatCompletionResult, ToolCall, Usage

from .test_api_agents import _automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize


def _llm_result(*, tool_calls=None, text=None) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _make_artifact(client) -> tuple[dict, dict]:
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact").json()
    return process, artifact


def _scenario_payload(**overrides) -> dict:
    payload = {
        "name": "Happy path",
        "inputs": {"record_id": "abc"},
        "system_stubs": {
            "CRM API": {"mode": "static", "static_responses": [{"match": {}, "response": {"tier": "gold"}}]}
        },
        "human_checkpoint_config": {"mode": "probability", "approve_probability": 1.0},
        "expected_steps": [
            {"kind": "tool_call", "target": "CRM API"},
            {"kind": "human_checkpoint", "expected_decision": "approve"},
        ],
        "expected_outputs": {"summary": None},
    }
    payload.update(overrides)
    return payload


def test_create_and_list_scenario(client):
    process, artifact = _make_artifact(client)

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    )
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "Happy path"
    assert body["agent_artifact_id"] == artifact["id"]

    listed = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios").json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]


def test_create_scenario_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)

    r = viewer_client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    )
    assert r.status_code == 403

    r = viewer_client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios")
    assert r.status_code == 200


def test_delete_scenario(client):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    r = client.delete(f"/api/processes/{process['id']}/scenarios/{scenario['id']}")
    assert r.status_code == 204

    listed = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios").json()
    assert listed == []


def test_run_scenario_end_to_end_and_summary(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    # First call: schema inference for the artifact's one system, "CRM API".
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
    fake_llm.complete.side_effect = [
        _llm_result(text=json.dumps(schema_payload)),
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "abc"})]),
        _llm_result(
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="request_human_decision",
                    arguments={"summary": "Approve?", "proposed_action": {"tier": "gold"}},
                )
            ]
        ),
        _llm_result(text=json.dumps({"summary": "done"})),
    ]

    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 200
    run = r.json()
    assert run["status"] == "passed"
    assert run["deviations"] == []
    assert run["final_output"] == {"summary": "done"}
    assert [step["kind"] for step in run["trace"]] == ["tool_call", "human_checkpoint"]

    runs = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/runs").json()
    assert len(runs) == 1
    assert runs[0]["id"] == run["id"]

    summary = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/twin-summary").json()
    assert summary["run_count"] == 1
    assert summary["pass_rate"] == 1.0


def test_run_scenario_invalid_schema_inference_returns_502(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    fake_llm.complete.return_value = _llm_result(text="not json")

    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 502


def test_run_scenario_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    r = viewer_client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 403


def test_twin_summary_with_no_runs(client):
    process, artifact = _make_artifact(client)

    summary = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/twin-summary").json()
    assert summary["run_count"] == 0
    assert summary["pass_rate"] is None


def _run_happy_path_scenario(client, fake_llm, process, artifact, scenario) -> dict:
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
    fake_llm.complete.side_effect = [
        _llm_result(text=json.dumps(schema_payload)),
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "abc"})]),
        _llm_result(
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="request_human_decision",
                    arguments={"summary": "Approve?", "proposed_action": {"tier": "gold"}},
                )
            ]
        ),
        _llm_result(text=json.dumps({"summary": "done"})),
    ]
    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 200
    return r.json()


# -- US14.5: manual baseline comparison ----------------------------------------


def test_set_and_get_baseline(client):
    process, artifact = _make_artifact(client)

    r = client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline",
        json={"typical_time_seconds": 300, "error_rate": 0.2, "notes": "Manual estimate from the ops team."},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["typical_time_seconds"] == 300
    assert body["error_rate"] == 0.2

    summary = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/twin-summary").json()
    assert summary["baseline"]["typical_time_seconds"] == 300
    # No runs yet -- a baseline alone is not a comparison.
    assert summary["baseline_comparison"] is None


def test_baseline_error_rate_out_of_range_rejected(client):
    process, artifact = _make_artifact(client)

    r = client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline", json={"error_rate": 1.5}
    )
    assert r.status_code == 422


def test_baseline_comparison_after_a_run(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()
    client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline",
        json={"typical_time_seconds": 300, "error_rate": 0.5},
    )

    _run_happy_path_scenario(client, fake_llm, process, artifact, scenario)

    summary = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/twin-summary").json()
    comparison = summary["baseline_comparison"]
    assert comparison is not None
    assert comparison["average_run_duration_seconds"] is not None
    assert comparison["time_delta_seconds"] is not None
    # pass_rate is 1.0 for this run -> error_rate (0.0) - baseline 0.5 = -0.5.
    assert comparison["error_rate_delta"] == pytest.approx(-0.5)


def test_delete_baseline(client):
    process, artifact = _make_artifact(client)
    client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline", json={"error_rate": 0.1}
    )

    r = client.delete(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline")
    assert r.status_code == 204

    summary = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/twin-summary").json()
    assert summary["baseline"] is None


def test_set_baseline_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)

    r = viewer_client.put(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/baseline", json={"error_rate": 0.1}
    )
    assert r.status_code == 403
