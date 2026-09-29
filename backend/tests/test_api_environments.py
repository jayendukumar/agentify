"""Epic 21: Digital Twin Environments CRUD (US21.1/US21.4), the
fault-injection catalog (US21.2), and scenarios referencing an environment
end to end through a real run (US21.3)."""

import json

from app.llm.types import ChatCompletionResult, ToolCall, Usage

from .test_api_twin import _await_terminal_twin_run, _make_artifact


def _llm_result(*, tool_calls=None, text=None) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _environment_payload(**overrides) -> dict:
    payload = {
        "name": "Onboarding Twin v1",
        "systems": ["CRM API"],
        "datasets": {"synthetic_customers": [{"id": "abc", "tier": "gold"}]},
        "personas": {"customer": {"role": "customer"}},
        "events": [],
        "faults": {},
    }
    payload.update(overrides)
    return payload


def test_create_list_update_delete_environment(client):
    process = client.post("/api/processes", json={"name": "P"}).json()

    created = client.post(f"/api/processes/{process['id']}/twin-environments", json=_environment_payload())
    assert created.status_code == 201
    env = created.json()
    assert env["name"] == "Onboarding Twin v1"
    assert env["version"] == 1

    listed = client.get(f"/api/processes/{process['id']}/twin-environments").json()
    assert len(listed) == 1
    assert listed[0]["id"] == env["id"]

    updated = client.put(
        f"/api/processes/{process['id']}/twin-environments/{env['id']}",
        json=_environment_payload(name="Onboarding Twin v2"),
    ).json()
    assert updated["name"] == "Onboarding Twin v2"
    assert updated["version"] == 2

    r = client.delete(f"/api/processes/{process['id']}/twin-environments/{env['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/processes/{process['id']}/twin-environments").json() == []


def test_create_environment_requires_editor(client, viewer_client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = viewer_client.post(f"/api/processes/{process['id']}/twin-environments", json=_environment_payload())
    assert r.status_code == 403


def test_scenario_pins_environment_version_at_creation(client):
    process, artifact = _make_artifact(client)
    env = client.post(f"/api/processes/{process['id']}/twin-environments", json=_environment_payload()).json()

    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios",
        json={"name": "S", "environment_id": env["id"]},
    ).json()
    assert scenario["environment_id"] == env["id"]
    assert scenario["environment_version"] == 1


def test_run_scenario_with_environment_fault_fails_the_tool_step(client, fake_llm):
    """US21.2/US21.3 end to end: an environment declaring an http_500 fault
    (probability 1.0) for the artifact's one system intercepts the call --
    the agent never gets a real Proxy-mode response, and the run's trace
    shows the simulated fault."""
    process, artifact = _make_artifact(client)
    env = client.post(
        f"/api/processes/{process['id']}/twin-environments",
        json=_environment_payload(faults={"CRM API": [{"type": "http_500", "probability": 1.0}]}),
    ).json()
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios",
        json={"name": "CRM unavailable", "inputs": {"record_id": "abc"}, "environment_id": env["id"]},
    ).json()

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
        _llm_result(text=json.dumps({"summary": "handled the outage"})),
    ]

    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 202
    run = _await_terminal_twin_run(client, process["id"], r.json()["id"])

    tool_step = next(s for s in run["steps"] if s["type"] == "TOOL_FAILED")
    assert tool_step["reason"] == "Simulated fault: http_500"
    assert tool_step["result"]["error"] == "http_500"
    # Only two real LLM turns happened (the agent's tool call + its final
    # answer) -- no third call to a Proxy-mode simulation, proving the
    # fault short-circuited normal resolution rather than the response
    # coincidentally looking like an error.
    assert fake_llm.complete.call_count == 3  # schema inference + 2 agent turns
