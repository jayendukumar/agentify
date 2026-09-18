from datetime import datetime, timezone

from app.bpmn.builder import build_bpmn_xml
from app.schemas.blueprint import AgentSpec, BlueprintNodeResult, BlueprintOverlay
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _valid_xml(process_id: str) -> str:
    # A fully-connected diagram -- finalize (unlike generate) requires
    # validate_bpmn to come back clean, so a lone disconnected task
    # wouldn't do; the blueprint tests below don't care about the specific
    # element ids in here, only that finalize succeeds.
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


def test_generate_blueprint_without_finalized_version_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 400


def test_generate_blueprint_returns_501_once_finalized(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _finalize(client, process["id"])
    r = client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    assert r.status_code == 501


def test_get_blueprint_before_generate_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.get(f"/api/processes/{process['id']}/blueprint")
    assert r.status_code == 404


def _seed_blueprint(store, process_id, version_id):
    overlay = BlueprintOverlay(
        process_id=process_id,
        baseline_version_id=version_id,
        generated_at=datetime.now(timezone.utc),
        nodes=[
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
    store.set_blueprint(process_id, overlay)


def test_override_blueprint_node(client, store):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(store, process["id"], version["id"])

    r = client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/b",
        json={"verdict": "automatable", "justification": "Actually fine to automate with a review checkpoint"},
    )
    assert r.status_code == 200
    node = next(n for n in r.json()["nodes"] if n["node_id"] == "b")
    assert node["verdict"] == "automatable"
    assert node["overridden"] is True


def test_override_missing_node_returns_404(client, store):
    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(store, process["id"], version["id"])

    r = client.patch(
        f"/api/processes/{process['id']}/blueprint/nodes/does-not-exist",
        json={"verdict": "automatable", "justification": "x"},
    )
    assert r.status_code == 404


def test_export_blueprint_markdown(client, store):
    process = client.post("/api/processes", json={"name": "Export Test"}).json()
    version = _finalize(client, process["id"])
    _seed_blueprint(store, process["id"], version["id"])

    r = client.get(f"/api/processes/{process['id']}/blueprint/export")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "Data Fetch Agent" in r.text
    assert "Compliance requires a specific accountable human." in r.text
