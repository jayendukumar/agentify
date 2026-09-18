from app.bpmn.builder import build_bpmn_xml
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema, SourceRef


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


def _finalize(client, process_id, xml):
    r = client.put(f"/api/processes/{process_id}/bpmn", json={"xml": xml})
    assert r.status_code == 200
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


def test_finalize_rejects_malformed_xml(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    put_response = client.put(f"/api/processes/{process['id']}/bpmn", json={"xml": "<not-closed>"})
    assert put_response.status_code == 400  # rejected at PUT time now (US3.4), never becomes a draft
    r = client.post(f"/api/processes/{process['id']}/finalize")
    assert r.status_code == 400
