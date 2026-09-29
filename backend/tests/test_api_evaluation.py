"""Epic 19: multi-dimensional evaluation (US19.1), regression suites
(US19.2/US19.3), version comparison (US19.4), and agent-artifact version
numbering (US19.5)."""

import json

from app.llm.types import ChatCompletionResult, ToolCall, Usage

from .test_api_agents import _automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize
from .test_api_twin import _await_terminal_twin_run, _make_artifact, _scenario_payload

_SCHEMA_PAYLOAD = {
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


def _llm_result(*, tool_calls=None, text=None) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _happy_path_side_effect(*, infer_schema: bool = True) -> list:
    # Epic 14's TwinToolSchemaModel caches the inferred schema per artifact
    # -- a second run against the same, unchanged artifact skips that LLM
    # call entirely (app/db/repository.py's `_get_or_infer_tool_schemas`),
    # so a second happy-path run in the same test needs one fewer response.
    responses = []
    if infer_schema:
        responses.append(_llm_result(text=json.dumps(_SCHEMA_PAYLOAD)))
    responses.extend(
        [
            _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_record", arguments={"record_id": "abc"})]),
            _llm_result(
                tool_calls=[
                    ToolCall(
                        id="c2", name="request_human_decision",
                        arguments={"summary": "Approve?", "proposed_action": {"tier": "gold"}},
                    )
                ]
            ),
            _llm_result(text=json.dumps({"summary": "done"})),
        ]
    )
    return responses


def _run_happy_path(client, fake_llm, process, scenario, *, infer_schema: bool = True) -> dict:
    fake_llm.complete.side_effect = _happy_path_side_effect(infer_schema=infer_schema)
    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    assert r.status_code == 202
    return _await_terminal_twin_run(client, process["id"], r.json()["id"])


# -- US19.5: version numbering -------------------------------------------------


def test_agent_artifact_version_starts_at_one_and_bumps_on_regenerate(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_automatable_node("a")])

    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact").json()
    assert artifact["version"] == 1

    regenerated = client.post(f"/api/processes/{process['id']}/blueprint/nodes/a/agent-artifact").json()
    assert regenerated["id"] == artifact["id"]
    assert regenerated["version"] == 2


# -- US19.1: multi-dimensional evaluation --------------------------------------


def test_completed_run_carries_a_twelve_dimension_evaluation(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    run = _run_happy_path(client, fake_llm, process, scenario)
    assert run["status"] == "COMPLETED"
    evaluation = run["evaluation"]
    assert evaluation is not None
    names = {d["name"] for d in evaluation["dimensions"]}
    assert names == {
        "task_completion", "policy_compliance", "tool_selection_correctness", "tool_argument_correctness",
        "output_correctness", "grounding", "resilience", "efficiency", "human_intervention", "latency",
        "token_usage", "cost",
    }
    assert evaluation["total_count"] == 12
    assert evaluation["passed_count"] == 12  # a clean happy-path run passes every dimension
    task_completion = next(d for d in evaluation["dimensions"] if d["name"] == "task_completion")
    assert task_completion["passed"] is True


# -- US19.2/US19.3: regression suites -------------------------------------------


def test_create_and_run_regression_suite(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    suite = client.post(
        f"/api/processes/{process['id']}/regression-suites",
        json={"name": "Happy paths", "kind": "twin", "agent_artifact_id": artifact["id"], "scenario_ids": [scenario["id"]]},
    ).json()
    assert suite["scenario_ids"] == [scenario["id"]]

    listed = client.get(f"/api/processes/{process['id']}/regression-suites").json()
    assert len(listed) == 1

    fake_llm.complete.side_effect = _happy_path_side_effect()
    started = client.post(f"/api/processes/{process['id']}/regression-suites/{suite['id']}/run")
    assert started.status_code == 202
    suite_run_id = started.json()["id"]

    import time

    deadline = time.monotonic() + 5.0
    result = None
    while time.monotonic() < deadline:
        result = client.get(f"/api/processes/{process['id']}/regression-suite-runs/{suite_run_id}").json()
        if result["status"] == "completed":
            break
        time.sleep(0.1)
    assert result is not None
    assert result["status"] == "completed"
    assert result["total_count"] == 1
    assert result["pass_count"] == 1
    assert result["scenario_results"][0]["scenario_id"] == scenario["id"]


def test_delete_regression_suite(client):
    process, artifact = _make_artifact(client)
    suite = client.post(
        f"/api/processes/{process['id']}/regression-suites",
        json={"name": "S", "kind": "twin", "agent_artifact_id": artifact["id"], "scenario_ids": []},
    ).json()

    r = client.delete(f"/api/processes/{process['id']}/regression-suites/{suite['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/processes/{process['id']}/regression-suites").json() == []


def test_create_regression_suite_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)
    r = viewer_client.post(
        f"/api/processes/{process['id']}/regression-suites",
        json={"name": "S", "kind": "twin", "agent_artifact_id": artifact["id"], "scenario_ids": []},
    )
    assert r.status_code == 403


# -- US19.4: run comparison -----------------------------------------------------


def test_compare_two_runs_reports_no_differences_when_identical(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()

    run_a = _run_happy_path(client, fake_llm, process, scenario)
    run_b = _run_happy_path(client, fake_llm, process, scenario, infer_schema=False)

    comparison = client.get(
        f"/api/processes/{process['id']}/runs/compare", params={"run_a": run_a["id"], "run_b": run_b["id"]}
    ).json()
    assert comparison["run_a_id"] == run_a["id"]
    assert comparison["run_b_id"] == run_b["id"]
    assert comparison["differences"] == ["No behavioral differences detected between these two runs."]


def test_compare_two_runs_reports_tool_and_output_differences(client, fake_llm):
    process, artifact = _make_artifact(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload()
    ).json()
    run_a = _run_happy_path(client, fake_llm, process, scenario)

    # Run B never calls the CRM tool and answers directly with a different output.
    fake_llm.complete.side_effect = [
        _llm_result(text=json.dumps({"summary": "different"})),
    ]
    r = client.post(f"/api/processes/{process['id']}/scenarios/{scenario['id']}/run")
    run_b = _await_terminal_twin_run(client, process["id"], r.json()["id"])

    comparison = client.get(
        f"/api/processes/{process['id']}/runs/compare", params={"run_a": run_a["id"], "run_b": run_b["id"]}
    ).json()
    joined = " ".join(comparison["differences"])
    assert "tools" in joined
    assert "output" in joined
