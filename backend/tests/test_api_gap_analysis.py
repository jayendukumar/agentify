import json

from app.db import repository
from app.db.session import get_session_factory
from app.llm.types import ChatCompletionResult, Usage
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _seed_schema(process_id: str) -> None:
    session = get_session_factory()()
    try:
        document = repository.add_document(session, process_id, "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        ref = SourceRef(document_id=document.id, location="p1", excerpt="x")
        schema = ProcessSchema(
            process_name="P",
            elements=[
                ProcessElement(id="a", type="task", label="Classify requirements", source_refs=[ref], confidence="high"),
                ProcessElement(id="b", type="task", label="Issue joining pack", source_refs=[ref], confidence="high"),
            ],
            flows=[ProcessFlow(id="f1", **{"from": "a"}, to="b")],
        )
        repository.merge_process_schema(session, process_id, schema)
        session.commit()
    finally:
        session.close()


def _llm_json(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _finding_payload(target_id: str = "a", diff: dict | None = None) -> dict:
    return {
        "findings": [
            {
                "kind": "structural",
                "question": "Is 'Classify requirements' the process start?",
                "target_element_ids": [target_id],
                "options": [{"label": "Yes, add a start event", "diff": diff}],
            }
        ]
    }


def test_analyze_on_process_with_no_schema_returns_empty_list(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    assert r.status_code == 200
    assert r.json() == []


def test_analyze_persists_findings(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(_finding_payload())

    r = client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    assert r.status_code == 200
    findings = r.json()
    assert len(findings) == 1
    assert findings[0]["status"] == "open"
    assert findings[0]["kind"] == "structural"

    r = client.get(f"/api/processes/{process['id']}/gap-findings")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_analyze_invalid_llm_json_returns_502(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = ChatCompletionResult(
        text="not json", finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="m"
    )

    r = client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    assert r.status_code == 502


def test_list_findings_filters_by_status(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(_finding_payload())
    client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    finding_id = client.get(f"/api/processes/{process['id']}/gap-findings").json()[0]["id"]

    client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/dismiss")

    r = client.get(f"/api/processes/{process['id']}/gap-findings", params={"status": "open"})
    assert r.json() == []
    r = client.get(f"/api/processes/{process['id']}/gap-findings", params={"status": "dismissed"})
    assert len(r.json()) == 1


def test_dismiss_finding(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(_finding_payload())
    client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    finding_id = client.get(f"/api/processes/{process['id']}/gap-findings").json()[0]["id"]

    r = client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/dismiss")
    assert r.status_code == 200
    assert r.json()["status"] == "dismissed"

    # Already-decided -- can't dismiss twice.
    r = client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/dismiss")
    assert r.status_code == 409


def test_resolve_finding_with_diff_applies_it_to_the_schema(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})

    diff = {
        "intent": "add_node",
        "summary": "Add a start event before Classify requirements.",
        "target_element_ids": ["a"],
        "operations": [
            {"op": "add_element", "element": {"id": "new-1", "type": "start_event", "label": "Start", "actor_id": None, "inputs": [], "outputs": [], "systems_touched": []}},
            {"op": "add_flow", "flow": {"from": "new-1", "to": "a", "condition": None}},
        ],
    }
    fake_llm.complete.return_value = _llm_json(_finding_payload(diff=diff))
    client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    finding_id = client.get(f"/api/processes/{process['id']}/gap-findings").json()[0]["id"]

    r = client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/resolve", json={"option_index": 0})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "resolved"
    assert body["chosen_option_label"] == "Yes, add a start event"

    updated = client.get(f"/api/processes/{process['id']}").json()
    labels = {e["label"] for e in updated["process_schema"]["elements"]}
    assert "Start" in labels


def test_resolve_finding_with_no_diff_just_marks_resolved(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(_finding_payload(diff=None))
    client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    finding_id = client.get(f"/api/processes/{process['id']}/gap-findings").json()[0]["id"]

    r = client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/resolve", json={"option_index": 0})
    assert r.status_code == 200
    assert r.json()["status"] == "resolved"


def test_resolve_finding_bad_option_index_returns_400(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(_finding_payload())
    client.post(f"/api/processes/{process['id']}/gap-findings/analyze")
    finding_id = client.get(f"/api/processes/{process['id']}/gap-findings").json()[0]["id"]

    r = client.post(f"/api/processes/{process['id']}/gap-findings/{finding_id}/resolve", json={"option_index": 5})
    assert r.status_code == 400


def test_resolve_unknown_finding_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/gap-findings/does-not-exist/resolve", json={"option_index": 0})
    assert r.status_code == 404
