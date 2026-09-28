import time

from .test_api_agents import _not_automatable_node, _seed_blueprint
from .test_api_blueprint import _finalize


def _make_process_with_manual_node(client) -> tuple[dict, dict]:
    """`_finalize`'s default diagram is Event_start -> Task_a -> Event_end
    (see test_api_blueprint.py's _valid_xml). Seeding Task_a as
    not_automatable means the orchestration walk needs zero LLM calls
    (pure manual-node simulation), so this fixture exercises the real
    background-task/DB-persistence path without needing a generated agent
    artifact or a configured fake_llm response."""
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"], [_not_automatable_node("Task_a")])
    return process, version


def _scenario_payload(**overrides) -> dict:
    payload = {
        "name": "Happy path",
        "inputs": {"record_id": "abc"},
        "gateway_decisions": {},
        "manual_node_config": {},
        "system_stubs": {},
        "human_checkpoint_config": {},
        "data_mapping_mode": {},
        "expected_path": [],
        "expected_final_output": {},
    }
    payload.update(overrides)
    return payload


def _await_terminal_run(client, process_id: str, run_id: str, *, timeout_seconds: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        run = client.get(f"/api/processes/{process_id}/orchestration/runs/{run_id}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.1)
    raise AssertionError(f"Orchestration run {run_id} did not finish within {timeout_seconds}s")


def test_create_list_delete_scenario(client):
    process, _version = _make_process_with_manual_node(client)

    r = client.post(f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload())
    assert r.status_code == 201
    scenario = r.json()
    assert scenario["name"] == "Happy path"
    assert scenario["process_id"] == process["id"]

    listed = client.get(f"/api/processes/{process['id']}/orchestration/scenarios").json()
    assert len(listed) == 1
    assert listed[0]["id"] == scenario["id"]

    r = client.delete(f"/api/processes/{process['id']}/orchestration/scenarios/{scenario['id']}")
    assert r.status_code == 204
    assert client.get(f"/api/processes/{process['id']}/orchestration/scenarios").json() == []


def test_create_scenario_requires_editor(client, viewer_client):
    process, _version = _make_process_with_manual_node(client)
    r = viewer_client.post(f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload())
    assert r.status_code == 403


def test_create_scenario_without_blueprint_returns_404(client):
    process = client.post("/api/processes", json={"name": "No blueprint"}).json()
    r = client.post(f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload())
    assert r.status_code == 404


def test_run_starts_and_eventually_completes(client):
    process, _version = _make_process_with_manual_node(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload()
    ).json()

    r = client.post(f"/api/processes/{process['id']}/orchestration/scenarios/{scenario['id']}/run")
    assert r.status_code == 202
    started = r.json()
    assert started["status"] == "running"
    assert started["scenario_id"] == scenario["id"]

    run = _await_terminal_run(client, process["id"], started["id"])
    assert run["status"] == "passed"
    # Event_start -> Task_a (manual, human-checkpoint default) -> Event_end.
    assert run["visited_path"] == ["Event_start", "Task_a", "Event_end"]
    kinds = [nr["kind"] for nr in run["node_runs"]]
    assert kinds == ["manual", "end_event"]
    assert run["node_runs"][0]["output"]["human_decision"] == "approve"

    listed = client.get(f"/api/processes/{process['id']}/orchestration/runs").json()
    assert len(listed) == 1
    assert listed[0]["id"] == run["id"]


def test_run_requires_editor(client, viewer_client):
    process, _version = _make_process_with_manual_node(client)
    scenario = client.post(
        f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload()
    ).json()
    r = viewer_client.post(f"/api/processes/{process['id']}/orchestration/scenarios/{scenario['id']}/run")
    assert r.status_code == 403
