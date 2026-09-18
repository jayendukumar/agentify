from app.bpmn.builder import build_bpmn_xml
from app.db import repository
from app.db.session import get_session_factory
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef

_MINIMAL_XML = '<root><task id="a" name="Step A"/></root>'  # not valid BPMN -- no <bpmn:process>


def _seed_schema(process_id: str) -> ProcessSchema:
    session = get_session_factory()()
    try:
        # source_refs.document_id is a real FK -- needs an actual document row.
        document = repository.add_document(session, process_id, "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        doc_id = document.id

        schema = ProcessSchema(
            process_name="P",
            actors=[Actor(id="a1", name="Employee", type="role")],
            elements=[
                ProcessElement(
                    id="e1", type="start_event", label="Start",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
                ProcessElement(
                    id="e2", type="task", label="Submit request", actor_id="a1",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
                ProcessElement(
                    id="e3", type="end_event", label="End",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
            ],
            flows=[
                ProcessFlow(id="f1", **{"from": "e1"}, to="e2"),
                ProcessFlow(id="f2", **{"from": "e2"}, to="e3"),
            ],
        )
        repository.merge_process_schema(session, process_id, schema)
        session.commit()
    finally:
        session.close()
    return schema


def test_generate_bpmn_without_extracted_schema_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})
    assert r.status_code == 400


def test_generate_bpmn_produces_valid_diagram(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])

    r = client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})
    assert r.status_code == 200
    body = r.json()
    assert "<bpmn:process" in body["xml"]
    assert "<bpmndi:BPMNDiagram" in body["xml"]
    assert body["low_confidence_element_ids"] == []  # all seeded elements are "high"
    assert body["validation_issues"] == []  # well-connected schema -- nothing to flag

    r = client.get(f"/api/processes/{process['id']}")
    assert r.json()["has_draft_bpmn"] is True


def test_generate_bpmn_flags_low_confidence_elements(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])

    low_conf_schema = ProcessSchema(
        process_name="P",
        elements=[
            ProcessElement(
                id="e9", type="task", label="Unclear step",
                # reuses the document row _seed_schema already created for this process
                source_refs=[SourceRef(document_id="doc-seed", location="p1", excerpt="x")], confidence="low",
            )
        ],
    )
    session = get_session_factory()()
    try:
        repository.merge_process_schema(session, process["id"], low_conf_schema)
        session.commit()
    finally:
        session.close()

    r = client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})
    # Generation succeeds -- it does NOT block on structural issues coming
    # from real (imperfectly merged) data, it surfaces them instead.
    assert r.status_code == 200
    body = r.json()
    assert "e9" in body["low_confidence_element_ids"]
    assert body["validation_issues"] != []  # e9 has no flows at all -- genuinely disconnected
    assert any("Task_e9" in issue for issue in body["validation_issues"])


def test_get_draft_before_any_generate_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.get(f"/api/processes/{process['id']}/bpmn")
    assert r.status_code == 404


def test_put_valid_bpmn_updates_draft(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    schema = _seed_schema(process["id"])
    valid_xml, _ = build_bpmn_xml(process["id"], schema)

    r = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": valid_xml})
    assert r.status_code == 200
    assert r.json()["xml"] == valid_xml
    assert r.json()["low_confidence_element_ids"] == []  # manual edits clear stale confidence flags

    r = client.get(f"/api/processes/{process['id']}/bpmn")
    assert r.status_code == 200
    assert r.json()["xml"] == valid_xml


def test_put_rejects_invalid_bpmn(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": _MINIMAL_XML})
    assert r.status_code == 400


def test_put_rejects_empty_xml(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": "   "})
    assert r.status_code == 400
