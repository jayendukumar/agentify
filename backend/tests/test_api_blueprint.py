import json

from app.bpmn.builder import build_bpmn_xml
from app.db import repository
from app.db.session import get_session_factory
from app.llm.types import ChatCompletionResult, Usage
from app.schemas.blueprint import AgentSpec, BlueprintNodeResult
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _valid_xml(process_id: str) -> str:
    # A fully-connected diagram -- finalize (unlike generate) requires
    # validate_bpmn to come back clean, so a lone disconnected task
    # wouldn't do. Node ids after mapping: Event_start, Task_a, Event_end.
    schema = ProcessSchema(
        process_name="P",
        elements=[
            ProcessElement(id="start", type="start_event", label="Start", source_refs=[_ref()], confidence="high"),
            ProcessElement(id="a", type="task", label="Step A", source_refs=[_ref()], confidence="high"),
            ProcessElement(id="end", type="end_event", label="End", source_refs=[_ref()], confidence="high"),
        ],
        flows=[
            ProcessFlow(id="f1", **{"from": "start"}, to="a"),
            ProcessFlow(id="f2", **{"from": "a"}, to="end"),
        ],
    )
    xml, _ = build_bpmn_xml(process_id, schema)
    return xml


def _finalize(client, process_id, xml=None):
    client.put(f"/api/processes/{process_id}/bpmn", json={"xml": xml or _valid_xml(process_id)})
    return client.post(f"/api/processes/{process_id}/finalize").json()


def _llm_json(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _node_result(node_id: str, **overrides) -> dict:
    result = {
        "node_id": node_id,
        "verdict": "automatable",
        "step_type": "data_retrieval_transformation",
        "rationale": f"{node_id} is a mechanical step with no judgment required.",
        "agent_spec": {
            "name": f"{node_id} Agent",
            "purpose": "Do the thing",
            "trigger": "Upstream step completes",
        },
        "not_automatable_reason": None,
    }
    result.update(overrides)
    return result


def _all_nodes_response() -> dict:
    return {
        "nodes": [
            _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="Process entry point, nothing to automate"),
            _node_result("Task_a"),
            _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="Process exit point, nothing to automate"),
        ]
    }


def test_generate_blueprint_without_finalized_version_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 400


def test_generate_blueprint_with_unknown_version_id_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])
    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={"version_id": "ver-nope"})
    assert r.status_code == 404


def test_generate_blueprint_produces_overlay_from_llm_response(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(_all_nodes_response())

    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["baseline_version_id"] == version["id"]
    node_ids = {n["node_id"] for n in body["nodes"]}
    assert node_ids == {"Event_start", "Task_a", "Event_end"}
    task_a = next(n for n in body["nodes"] if n["node_id"] == "Task_a")
    assert task_a["verdict"] == "automatable"
    assert task_a["agent_spec"]["name"] == "Task_a Agent"

    # Persisted -- a plain GET (no LLM call) returns the same overlay.
    r = client.get(f"/api/processes/{process['id']}/blueprint")
    assert r.status_code == 200
    assert {n["node_id"] for n in r.json()["nodes"]} == {"Event_start", "Task_a", "Event_end"}


def test_generate_blueprint_can_be_rerun_after_baseline_changes(client, fake_llm):
    # US7.7 -- regenerating (e.g. against a newer finalized version)
    # replaces the overlay in place rather than accumulating history.
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])
    fake_llm.complete.return_value = _llm_json(_all_nodes_response())
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})

    second_response = _all_nodes_response()
    second_response["nodes"][1]["rationale"] = "Re-evaluated after a baseline change."
    fake_llm.complete.return_value = _llm_json(second_response)
    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 200

    r = client.get(f"/api/processes/{process['id']}/blueprint")
    task_a = next(n for n in r.json()["nodes"] if n["node_id"] == "Task_a")
    assert task_a["rationale"] == "Re-evaluated after a baseline change."


def test_generate_blueprint_missing_node_coverage_returns_502(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])
    incomplete = _all_nodes_response()
    incomplete["nodes"].pop()  # drop Event_end's result
    fake_llm.complete.return_value = _llm_json(incomplete)

    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 502
    assert "Event_end" in r.json()["detail"]


def test_generate_blueprint_invalid_llm_json_returns_502(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])
    fake_llm.complete.return_value = ChatCompletionResult(
        text="not json", finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model"
    )

    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 502


def test_get_blueprint_before_generate_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.get(f"/api/processes/{process['id']}/blueprint")
    assert r.status_code == 404


def _seed_blueprint(process_id: str, version_id: str) -> None:
    session = get_session_factory()()
    try:
        repository.set_blueprint_overlay(
            session,
            process_id,
            version_id,
            [
                BlueprintNodeResult(
                    node_id="a",
                    verdict="automatable",
                    step_type="data_retrieval_transformation",
                    rationale="Structured data lookup with no judgment required.",
                    agent_spec=AgentSpec(
                        name="Data Fetch Agent",
                        purpose="Look up the record",
                        trigger="Upstream step completes",
                    ),
                ),
                BlueprintNodeResult(
                    node_id="b",
                    verdict="not_automatable",
                    step_type="approval_compliance_signoff",
                    rationale="Requires accountable sign-off.",
                    not_automatable_reason="Compliance requires a specific accountable human.",
                ),
            ],
        )
        session.commit()
    finally:
        session.close()


def test_override_blueprint_node(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"])

    r = client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/b",
        json={"verdict": "automatable", "justification": "Actually fine to automate with a review checkpoint"},
    )
    assert r.status_code == 200
    node = next(n for n in r.json()["nodes"] if n["node_id"] == "b")
    assert node["verdict"] == "automatable"
    assert node["overridden"] is True


def test_override_missing_node_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"])

    r = client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/does-not-exist",
        json={"verdict": "automatable", "justification": "x"},
    )
    assert r.status_code == 404


def test_export_blueprint_markdown(client):
    process = client.post("/api/processes", json={"name": "Export Test"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(process["id"], version["id"])

    r = client.get(f"/api/processes/{process['id']}/blueprint/export")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Data Fetch Agent" in r.text
    assert "Compliance requires a specific accountable human." in r.text
