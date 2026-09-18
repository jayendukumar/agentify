import json

from app.db import repository
from app.db.session import get_session_factory
from app.llm.types import ChatCompletionResult, Usage
from app.schemas.common import Actor, ProcessElement, ProcessFlow, ProcessSchema, SourceRef


def _seed_schema(process_id: str) -> ProcessSchema:
    session = get_session_factory()()
    try:
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


def _llm_json(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _seed_and_generate(client, process_id: str) -> None:
    _seed_schema(process_id)
    r = client.post(f"/api/processes/{process_id}/bpmn/generate", json={})
    assert r.status_code == 200


def test_chat_on_missing_process_returns_404(client):
    r = client.post("/api/processes/does-not-exist/chat/messages", json={"text": "hi"})
    assert r.status_code == 404


def test_chat_without_extracted_schema_returns_400(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "add a step"})
    assert r.status_code == 400


def test_list_chat_messages_empty_by_default(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    r = client.get(f"/api/processes/{process['id']}/chat/messages")
    assert r.status_code == 200
    assert r.json() == []


def test_explain_reply_has_no_diff(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(
        {"kind": "explain", "reply_text": "Submit request is done by the Employee role.", "diff": None}
    )

    r = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "who owns the submit step"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "explain"
    assert body["needs_confirmation"] is False
    assert body["proposed_diff"] is None
    assert body["applied"] is False

    history = client.get(f"/api/processes/{process['id']}/chat/messages").json()
    assert len(history) == 1


def test_clarify_reply_has_no_diff(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(
        {"kind": "clarify", "reply_text": "Do you mean 'Submit request' or 'Start'?", "diff": None}
    )

    r = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "rename this step"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "clarify"
    assert body["needs_confirmation"] is False


def test_edit_reply_needs_confirmation_and_translates_ids(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_schema(process["id"])
    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Rename 'Submit request' to 'Submit approval request'.",
            "diff": {
                "intent": "rename_node",
                "summary": "Rename 'Submit request' to 'Submit approval request'.",
                "target_element_ids": ["e2"],
                "operations": [
                    {"op": "update_element", "element_id": "e2", "fields": {"label": "Submit approval request"}}
                ],
            },
        }
    )

    r = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "rename the submit step"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "edit"
    assert body["needs_confirmation"] is True
    assert body["applied"] is False
    # bare schema id "e2" translated to its rendered BPMN id
    assert body["proposed_diff"]["target_element_ids"] == ["Task_e2"]
    assert body["proposed_diff"]["operations"][0]["element_id"] == "Task_e2"


def test_apply_confirm_updates_schema_and_draft_and_preserves_positions(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_and_generate(client, process["id"])
    original_xml = client.get(f"/api/processes/{process['id']}/bpmn").json()["xml"]
    assert 'bpmnElement="Task_e2"' in original_xml

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Rename 'Submit request' to 'Submit approval request'.",
            "diff": {
                "intent": "rename_node",
                "summary": "Rename 'Submit request' to 'Submit approval request'.",
                "target_element_ids": ["e2"],
                "operations": [
                    {"op": "update_element", "element_id": "e2", "fields": {"label": "Submit approval request"}}
                ],
            },
        }
    )
    message = client.post(
        f"/api/processes/{process['id']}/chat/messages", json={"text": "rename the submit step"}
    ).json()

    r = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r.status_code == 200
    applied = r.json()
    assert applied["applied"] is True
    assert applied["declined"] is False
    assert applied["decided_at"] is not None

    updated_process = client.get(f"/api/processes/{process['id']}").json()
    assert updated_process["process_schema"]["elements"][1]["label"] == "Submit approval request"

    updated_xml = client.get(f"/api/processes/{process['id']}/bpmn").json()["xml"]
    assert 'name="Submit approval request"' in updated_xml
    # unchanged nodes (e1, e3) kept their original DI bounds
    import re

    def bounds_for(xml: str, bpmn_id: str) -> str:
        shape = re.search(rf'bpmnElement="{bpmn_id}".*?/>', xml, re.DOTALL)
        return shape.group(0)

    assert bounds_for(original_xml, "Event_e1") == bounds_for(updated_xml, "Event_e1")
    assert bounds_for(original_xml, "Event_e3") == bounds_for(updated_xml, "Event_e3")


def test_apply_confirm_rejects_diff_that_would_orphan_a_node(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_and_generate(client, process["id"])

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Remove the submit step.",
            "diff": {
                "intent": "delete_node",
                "summary": "Remove the submit step.",
                "target_element_ids": ["e2"],
                # deliberately omits reconnecting e1 -> e3, per the skill's
                # "backend applies operations literally" rule -- this should
                # leave e3 with no incoming flow and fail validation.
                "operations": [{"op": "remove_element", "element_id": "e2"}],
            },
        }
    )
    message = client.post(
        f"/api/processes/{process['id']}/chat/messages", json={"text": "remove the submit step"}
    ).json()

    r = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r.status_code == 400

    # nothing persisted -- message still pending, schema/draft untouched
    pending = client.get(f"/api/processes/{process['id']}/chat/messages").json()[0]
    assert pending["applied"] is False
    assert pending["declined"] is False
    updated_process = client.get(f"/api/processes/{process['id']}").json()
    assert len(updated_process["process_schema"]["elements"]) == 3


def test_apply_confirm_succeeds_despite_preexisting_unrelated_validation_issues(client, fake_llm):
    # Reproduces a real scenario found via manual testing: a generated
    # draft from imperfect extraction can have no start/end events at all
    # (POST /bpmn/generate deliberately doesn't block on that -- see
    # app/api/bpmn.py), which trips validate_bpmn's orphan-node check on
    # the *original* diagram already, before any chat edit. Chat-apply
    # must not block on that pre-existing, unrelated issue.
    process = client.post("/api/processes", json={"name": "P"}).json()
    session = get_session_factory()()
    try:
        document = repository.add_document(session, process["id"], "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        doc_id = document.id
        schema = ProcessSchema(
            process_name="P",
            elements=[
                ProcessElement(
                    id="e1", type="task", label="Place order",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
                ProcessElement(
                    id="e2", type="task", label="Pack order",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
            ],
            flows=[ProcessFlow(id="f1", **{"from": "e1"}, to="e2")],
        )
        repository.merge_process_schema(session, process["id"], schema)
        session.commit()
    finally:
        session.close()
    r = client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})
    assert r.status_code == 200
    assert r.json()["validation_issues"] != []  # confirms the pre-existing issue this test is about

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Rename 'Place order'.",
            "diff": {
                "intent": "rename_node",
                "summary": "Rename 'Place order'.",
                "target_element_ids": ["e1"],
                "operations": [{"op": "update_element", "element_id": "e1", "fields": {"label": "Customer places order"}}],
            },
        }
    )
    message = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "rename e1"}).json()

    r = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["applied"] is True


def test_apply_confirm_succeeds_appending_a_node_to_an_open_ended_chain(client, fake_llm):
    # Reproduces a real scenario found via manual testing: appending a node
    # after a last-step task with no explicit end_event "moves" the
    # no-outgoing-flow complaint from the old last node (now fixed) to the
    # new one (not yet an end event either) -- same issue count, different
    # node id in the message text. A strict "any new issue string" check
    # wrongly blocked this net-neutral edit; only a real count increase
    # should block.
    process = client.post("/api/processes", json={"name": "P"}).json()
    session = get_session_factory()()
    try:
        document = repository.add_document(session, process["id"], "doc-seed", "sop.docx", "application/octet-stream", 1)
        session.commit()
        doc_id = document.id
        schema = ProcessSchema(
            process_name="P",
            elements=[
                ProcessElement(
                    id="e1", type="task", label="Place order",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
                ProcessElement(
                    id="e2", type="task", label="Pack order",
                    source_refs=[SourceRef(document_id=doc_id, location="p1", excerpt="x")], confidence="high",
                ),
            ],
            flows=[ProcessFlow(id="f1", **{"from": "e1"}, to="e2")],
        )
        repository.merge_process_schema(session, process["id"], schema)
        session.commit()
    finally:
        session.close()
    r = client.post(f"/api/processes/{process['id']}/bpmn/generate", json={})
    assert r.status_code == 200
    pre_existing_issue_count = len(r.json()["validation_issues"])
    assert pre_existing_issue_count == 2  # e1 has no incoming, e2 has no outgoing

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Added 'Ship order' after packing.",
            "diff": {
                "intent": "add_node",
                "summary": "Added 'Ship order' after packing.",
                "target_element_ids": ["e2"],
                "operations": [
                    {"op": "add_element", "element": {"id": "new-1", "type": "task", "label": "Ship order", "actor_id": None, "inputs": [], "outputs": [], "systems_touched": []}},
                    {"op": "add_flow", "flow": {"from": "e2", "to": "new-1", "condition": None}},
                ],
            },
        }
    )
    message = client.post(f"/api/processes/{process['id']}/chat/messages", json={"text": "add a ship step after packing"}).json()

    r = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r.status_code == 200
    assert r.json()["applied"] is True

    after = client.get(f"/api/processes/{process['id']}/bpmn").json()
    assert len(after["validation_issues"]) == pre_existing_issue_count  # moved, not added to


def test_apply_decline_does_not_change_diagram(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_and_generate(client, process["id"])
    original_xml = client.get(f"/api/processes/{process['id']}/bpmn").json()["xml"]

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Rename 'Submit request'.",
            "diff": {
                "intent": "rename_node",
                "summary": "Rename 'Submit request'.",
                "target_element_ids": ["e2"],
                "operations": [{"op": "update_element", "element_id": "e2", "fields": {"label": "Renamed"}}],
            },
        }
    )
    message = client.post(
        f"/api/processes/{process['id']}/chat/messages", json={"text": "rename the submit step"}
    ).json()

    r = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": False})
    assert r.status_code == 200
    declined = r.json()
    assert declined["declined"] is True
    assert declined["applied"] is False

    assert client.get(f"/api/processes/{process['id']}/bpmn").json()["xml"] == original_xml


def test_apply_already_decided_message_returns_409(client, fake_llm):
    process = client.post("/api/processes", json={"name": "P"}).json()
    _seed_and_generate(client, process["id"])

    fake_llm.complete.return_value = _llm_json(
        {
            "kind": "edit",
            "reply_text": "Rename 'Submit request'.",
            "diff": {
                "intent": "rename_node",
                "summary": "Rename 'Submit request'.",
                "target_element_ids": ["e2"],
                "operations": [{"op": "update_element", "element_id": "e2", "fields": {"label": "Renamed"}}],
            },
        }
    )
    message = client.post(
        f"/api/processes/{process['id']}/chat/messages", json={"text": "rename the submit step"}
    ).json()

    r1 = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r1.status_code == 200

    r2 = client.post(f"/api/processes/{process['id']}/chat/messages/{message['id']}/apply", json={"confirm": True})
    assert r2.status_code == 409


def test_apply_unknown_message_returns_404(client):
    process = client.post("/api/processes", json={"name": "P"}).json()
    r = client.post(f"/api/processes/{process['id']}/chat/messages/msg_fake/apply", json={"confirm": True})
    assert r.status_code == 404
