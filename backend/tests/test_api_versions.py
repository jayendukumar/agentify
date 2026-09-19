from app.bpmn.builder import build_bpmn_xml
from app.db import repository
from app.db.session import get_session_factory
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _ref() -> SourceRef:
    return SourceRef(document_id="d", location="p1", excerpt="x")


def _schema(task_b_id: str, task_b_label: str, task_a_label: str = "Step A") -> ProcessSchema:
    return ProcessSchema(
        process_name="P",
        elements=[
            ProcessElement(id="start", type="start_event", label="Start", source_refs=[_ref()], confidence="high"),
            ProcessElement(id="a", type="task", label=task_a_label, source_refs=[_ref()], confidence="high"),
            ProcessElement(id=task_b_id, type="task", label=task_b_label, source_refs=[_ref()], confidence="high"),
            ProcessElement(id="end", type="end_event", label="End", source_refs=[_ref()], confidence="high"),
        ],
        flows=[
            ProcessFlow(id="f1", **{"from": "start"}, to="a"),
            ProcessFlow(id="f2", **{"from": "a"}, to=task_b_id),
            ProcessFlow(id="f3", **{"from": task_b_id}, to="end"),
        ],
    )


def _xml_v1(process_id: str) -> str:
    xml, _ = build_bpmn_xml(process_id, _schema("b", "Step B"))
    return xml


def _xml_v2(process_id: str) -> str:
    xml, _ = build_bpmn_xml(process_id, _schema("c", "Step C", task_a_label="Step A Renamed"))
    return xml


def _mark_gap_analysis_completed(process_id: str) -> None:
    # Epic 11: Finalize now requires gap analysis to have run at least
    # once (app/api/versions.py's gap_analysis_completed_at gate) --
    # these tests aren't exercising gap analysis itself (see
    # test_api_gap_analysis.py for that), so seed the flag directly
    # rather than wiring a real LLM call through every finalize test
    # here, same "seed via a direct session" pattern this file already
    # uses for schemas (test_finalize_error_uses_element_labels_not_raw_bpmn_ids).
    session = get_session_factory()()
    try:
        repository.mark_gap_analysis_completed(session, process_id)
        session.commit()
    finally:
        session.close()


def _finalize(client, process_id, xml):
    r = client.put(f"/api/processes/{process_id}/bpmn", json={"xml": xml})
    assert r.status_code == 200
    _mark_gap_analysis_completed(process_id)
    r = client.post(f"/api/processes/{process_id}/finalize")
    assert r.status_code == 201
    return r.json()


def test_finalize_without_draft_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400


def test_finalize_creates_version_and_lists_it(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    xml_v1 = _xml_v1(process["id"])
    version = _finalize(client, process["id"], xml_v1)

    r = client.get(f"/api/processes/{process['id']}/versions")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["id"] == version["id"]

    r = client.get(f"/api/processes/{process['id']}/versions/{version['id']}")
    assert r.status_code == 200
    assert r.json()["xml"] == xml_v1

    r = client.get(f"/api/processes/{process['id']}")
    assert r.json()["finalized_version_count"] == 1


def test_restore_version_sets_draft(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    xml_v1 = _xml_v1(process["id"])
    v1 = _finalize(client, process["id"], xml_v1)
    client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": _xml_v2(process["id"])})

    r = client.post(f"/api/processes/{process['id']}/versions/{v1['id']}/restore")
    assert r.status_code == 200

    r = client.get(f"/api/processes/{process['id']}/bpmn")
    assert r.json()["xml"] == xml_v1


def test_diff_versions(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    v1 = _finalize(client, process["id"], _xml_v1(process["id"]))
    v2 = _finalize(client, process["id"], _xml_v2(process["id"]))

    r = client.get(
        f"/api/processes/{process['id']}/versions/diff",
        params={"from_version_id": v1["id"], "to_version_id": v2["id"]},
    )
    assert r.status_code == 200
    result = r.json()
    assert result["added_element_ids"] == ["Task_c"]
    assert result["removed_element_ids"] == ["Task_b"]
    assert result["changed_element_ids"] == ["Task_a"]


def test_diff_versions_includes_readable_labels(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    v1 = _finalize(client, process["id"], _xml_v1(process["id"]))
    v2 = _finalize(client, process["id"], _xml_v2(process["id"]))

    r = client.get(
        f"/api/processes/{process['id']}/versions/diff",
        params={"from_version_id": v1["id"], "to_version_id": v2["id"]},
    )
    assert r.status_code == 200
    labels = r.json()["labels"]
    assert labels["Task_c"] == "Step C"  # added -- label comes from the "to" version
    assert labels["Task_b"] == "Step B"  # removed -- label comes from the "from" version
    assert labels["Task_a"] == "Step A Renamed"  # changed -- label comes from the "to" version


def test_finalize_rejects_malformed_xml(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    put_response = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": "<not-closed>"})
    assert put_response.status_code == 400  # rejected at PUT time now (US3.4), never becomes a draft
    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400


def test_finalize_blocked_when_gap_analysis_never_run(client):
    # Epic 11: a structurally valid draft (no content-completeness or
    # integrity issues at all) still can't finalize if gap analysis has
    # never completed for this process -- "no findings" and "never
    # checked" are deliberately different states (see
    # gap_analysis_completed_at's docstring, app/db/models.py).
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": _xml_v1(process["id"])})
    assert r.status_code == 200

    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400
    assert "gap analysis" in r.json()["detail"].lower()


def test_finalize_blocked_by_open_gap_finding(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": _xml_v1(process["id"])})
    assert r.status_code == 200
    _mark_gap_analysis_completed(process["id"])

    session = get_session_factory()()
    try:
        repository.add_gap_finding(
            session,
            process["id"],
            kind="structural",
            question="Is 'Step A' really the first step?",
            target_element_ids=["Task_a"],
            options=[],
        )
        session.commit()
    finally:
        session.close()

    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Is 'Step A' really the first step?" in detail


def test_finalize_error_uses_element_labels_for_integrity_issues(client):
    # Content-completeness issues ("no incoming flow") moved to Epic 11's
    # gap analysis and no longer block Finalize directly (see the two
    # tests above) -- what's left is the deterministic integrity backstop
    # (app/bpmn/validation.py's validate_bpmn_integrity), which should
    # never fire through a normal PUT/generate flow if app/bpmn/builder.py
    # is correct (PUT /bpmn already rejects any issue up front). Simulate
    # a builder regression directly via the DB, the same "seed via a
    # direct session" pattern this file already uses for schemas, to prove
    # the backstop still humanizes raw ids when it does fire.
    process = client.post("/api/processes", json={"name": "P"}).json()
    xml = _xml_v1(process["id"])
    # Corrupt a real generated diagram's DI: drop Task_a's BPMNShape entirely,
    # leaving its semantic element intact -- "Elements with no DI shape".
    import re

    corrupted = re.sub(
        r'<bpmndi:BPMNShape[^>]*bpmnElement="Task_a".*?</bpmndi:BPMNShape>', "", xml, flags=re.DOTALL
    )
    assert corrupted != xml

    session = get_session_factory()()
    try:
        document = repository.add_document(session, process["id"], "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        ref = SourceRef(document_id=document.id, location="p1", excerpt="x")
        schema = ProcessSchema(
            process_name="P",
            elements=[
                ProcessElement(id="start", type="start_event", label="Start", source_refs=[ref], confidence="high"),
                ProcessElement(id="a", type="task", label="Step A", source_refs=[ref], confidence="high"),
                ProcessElement(id="b", type="task", label="Step B", source_refs=[ref], confidence="high"),
                ProcessElement(id="end", type="end_event", label="End", source_refs=[ref], confidence="high"),
            ],
            flows=[
                ProcessFlow(id="f1", **{"from": "start"}, to="a"),
                ProcessFlow(id="f2", **{"from": "a"}, to="b"),
                ProcessFlow(id="f3", **{"from": "b"}, to="end"),
            ],
        )
        repository.merge_process_schema(session, process["id"], schema)
        repository.set_draft_bpmn(session, process["id"], corrupted, [])
        repository.mark_gap_analysis_completed(session, process["id"])
        session.commit()
    finally:
        session.close()

    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert "Step A" in detail
    assert "Task_a" not in detail
