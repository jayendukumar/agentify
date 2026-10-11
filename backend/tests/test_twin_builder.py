"""Epics 24/25: the guided Digital Twin test builder -- the deterministic
blueprint-derived context, and the LLM-backed drafting endpoints'
reconciliation of whatever the model echoes back against known-valid
names."""

import json
from unittest.mock import AsyncMock

import pytest

from app.llm.types import ChatCompletionResult, Usage
from app.schemas.agents import AgentDefinition
from app.schemas.blueprint import AgentIOField
from app.twin.test_builder import build_context, suggest_situations

from .test_api_twin import _make_artifact, _scenario_payload

_NS = 'xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL"'

_GATEWAY_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<bpmn:definitions {_NS} id="d">
  <bpmn:process id="p">
    <bpmn:startEvent id="Event_start" name="Invoice received"/>
    <bpmn:task id="Task_check" name="Check invoice"/>
    <bpmn:exclusiveGateway id="Gateway_amt" name="Amount over limit?"/>
    <bpmn:task id="Task_approve" name="Manager approval"/>
    <bpmn:task id="Task_post" name="Post to ERP"/>
    <bpmn:sequenceFlow id="f1" sourceRef="Event_start" targetRef="Task_check"/>
    <bpmn:sequenceFlow id="f2" sourceRef="Task_check" targetRef="Gateway_amt"/>
    <bpmn:sequenceFlow id="f3" sourceRef="Gateway_amt" targetRef="Task_approve">
      <bpmn:conditionExpression>Yes</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
    <bpmn:sequenceFlow id="f4" sourceRef="Gateway_amt" targetRef="Task_post">
      <bpmn:conditionExpression>No</bpmn:conditionExpression>
    </bpmn:sequenceFlow>
  </bpmn:process>
</bpmn:definitions>"""


def _definition(**overrides) -> AgentDefinition:
    defaults = dict(
        name="Invoice Check Agent",
        purpose="Check incoming invoices",
        trigger="Invoice received",
        system_prompt="x",
        input_schema=[AgentIOField(name="amount", source_or_destination="invoice", format="number")],
        output_schema=[AgentIOField(name="decision", source_or_destination="ERP", format="string")],
        tools_systems_needed=["ERP", "Vendor DB"],
        human_checkpoint="review_before_action",
        model="test-model",
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


def _llm_json(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload),
        finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


# -- build_context ---------------------------------------------------------------


def test_build_context_derives_gateway_branches_and_coverage_targets():
    context = build_context(
        artifact_id="agent_1", definition=_definition(), node_ids=["Task_check"], baseline_xml=_GATEWAY_XML
    )

    assert context.step_labels == ["Check invoice"]
    assert len(context.gateways) == 1
    gateway = context.gateways[0]
    assert gateway.label == "Amount over limit?"
    assert [(b.target_label, b.condition) for b in gateway.branches] == [
        ("Manager approval", "Yes"),
        ("Post to ERP", "No"),
    ]
    assert [t.id for t in context.coverage_targets] == [
        "happy_path",
        "gw:Gateway_amt:Task_approve",
        "gw:Gateway_amt:Task_post",
        "checkpoint:approve",
        "checkpoint:reject",
        "sys:ERP",
        "sys:Vendor DB",
        "data_quality",
    ]


def test_build_context_without_baseline_or_checkpoint_still_works():
    context = build_context(
        artifact_id="agent_1",
        definition=_definition(human_checkpoint="none", input_schema=[]),
        node_ids=["Task_check"],
        baseline_xml=None,
    )
    assert context.gateways == []
    assert [t.kind for t in context.coverage_targets] == ["happy_path", "system_failure", "system_failure"]


# -- API -------------------------------------------------------------------------


def test_context_endpoint_returns_artifact_context(client):
    process, artifact = _make_artifact(client)
    r = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/context")
    assert r.status_code == 200
    body = r.json()
    assert body["systems"] == ["CRM API"]
    assert body["human_checkpoint"] == "review_after_action"
    assert "sys:CRM API" in [t["id"] for t in body["coverage_targets"]]


def test_suggest_drops_names_the_model_invented(client, fake_llm):
    process, artifact = _make_artifact(client)
    fake_llm.complete.return_value = _llm_json(
        {
            "situations": [
                {
                    "title": "CRM is down on a busy morning",
                    "given": "Record R-1 arrives",
                    "when": "the CRM is unavailable",
                    "then": "the agent escalates",
                    "covers": ["sys:CRM API", "gw:made-up"],
                    "inputs": {"record_id": "R-1", "invented_field": 1},
                    "system_behaviour": {"CRM API": "down", "Billing": "down"},
                    "reviewer": {"behaviour": "reject"},
                    "expect": {"systems_used": ["CRM API", "Billing"], "review_decision": "reject", "output_fields": ["summary", "nope"]},
                },
                {"title": ""},  # unusable -- dropped
            ]
        }
    )

    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/suggest", json={"count": 3}
    )
    assert r.status_code == 200
    situations = r.json()["situations"]
    assert len(situations) == 1
    story = situations[0]["story"]
    assert story["source"] == "suggested"
    assert story["covers"] == ["sys:CRM API"]
    assert story["system_behaviour"] == {"CRM API": "down"}
    assert story["reviewer"]["behaviour"] == "reject"
    assert story["expect"] == {"systems_used": ["CRM API"], "review_decision": "reject", "output_fields": ["summary"]}
    assert situations[0]["inputs"] == {"record_id": "R-1"}
    assert fake_llm.complete.call_args.kwargs["operation"] == "twin_builder_suggest"


def test_suggest_with_malformed_llm_output_is_502(client, fake_llm):
    process, artifact = _make_artifact(client)
    fake_llm.complete.return_value = ChatCompletionResult(
        text="not json", finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="m"
    )
    r = client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/suggest", json={})
    assert r.status_code == 502


def test_suggest_requires_editor(client, viewer_client):
    process, artifact = _make_artifact(client)
    r = viewer_client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/suggest", json={})
    assert r.status_code == 403


def test_describe_returns_one_situation(client, fake_llm):
    process, artifact = _make_artifact(client)
    fake_llm.complete.return_value = _llm_json(
        {"situations": [{"title": "Gold customer lookup", "system_behaviour": {"CRM API": "custom_data"},
                          "system_data": {"CRM API": {"tier": "gold"}}}]}
    )
    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/describe",
        json={"description": "a gold-tier customer record"},
    )
    assert r.status_code == 200
    story = r.json()["story"]
    assert story["source"] == "described"
    assert story["system_data"] == {"CRM API": {"tier": "gold"}}


def test_generate_data_validates_mode_inputs_before_calling_llm(client, fake_llm):
    process, artifact = _make_artifact(client)
    url = f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/data"
    for body in ({"mode": "describe"}, {"mode": "mutate"}, {"mode": "clone"}):
        assert client.post(url, json=body).status_code == 400
    fake_llm.complete.assert_not_called()


def test_generate_data_returns_records_capped_to_count(client, fake_llm):
    process, artifact = _make_artifact(client)
    fake_llm.complete.return_value = _llm_json(
        {"records": [{"label": f"r{i}", "note": "", "record": {"record_id": f"R-{i}"}} for i in range(5)]}
    )
    r = client.post(
        f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/data",
        json={"mode": "messy", "count": 2},
    )
    assert r.status_code == 200
    assert [rec["label"] for rec in r.json()["records"]] == ["r0", "r1"]
    assert fake_llm.complete.call_args.kwargs["operation"] == "twin_data_messy"


def test_generate_personas_turns_uncheckable_threshold_into_reject(client, fake_llm):
    """The fixture agent's only input is a string `record_id` -- a
    threshold on any field can't be checked reliably (the engine raises if
    the field is absent from the approval request), so a strict reviewer
    becomes "rejects" rather than a run that can't finish."""
    process, artifact = _make_artifact(client)
    fake_llm.complete.return_value = _llm_json(
        {
            "personas": [
                {"name": "Sam", "role": "Finance Manager", "description": "Strict",
                 "reviewer": {"behaviour": "threshold", "field": "amount", "threshold": 10000}},
                {"name": "Gil", "role": "Clerk", "description": "Easy-going", "reviewer": {"behaviour": "approve"}},
            ]
        }
    )
    r = client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/test-builder/personas", json={})
    assert r.status_code == 200
    reviewers = [p["reviewer"] for p in r.json()["personas"]]
    assert reviewers[0] == {"behaviour": "reject", "field": None, "threshold": None, "persona_name": "Sam"}
    assert reviewers[1]["behaviour"] == "approve"
    assert "never use a 'threshold' reviewer" in fake_llm.complete.call_args.args[0][0].content


@pytest.mark.asyncio
async def test_threshold_reviewer_kept_only_on_numeric_input_field():
    context = build_context(artifact_id="agent_1", definition=_definition(), node_ids=["Task_check"], baseline_xml=None)
    llm = AsyncMock()
    llm.complete.return_value = _llm_json(
        {
            "situations": [
                {"title": "Over the limit", "reviewer": {"behaviour": "threshold", "field": "amount", "threshold": 5000}},
                {"title": "Wrong field", "reviewer": {"behaviour": "threshold", "field": "decision", "threshold": 1}},
            ]
        }
    )
    situations = await suggest_situations(llm, context, count=2)
    assert situations[0].story.reviewer.model_dump() == {
        "behaviour": "threshold", "field": "amount", "threshold": 5000.0, "persona_name": None,
    }
    assert situations[1].story.reviewer.behaviour == "reject"
    prompt = llm.complete.call_args.args[0][0].content
    assert "numeric input fields: amount" in prompt
    assert "Today's date is" in prompt


def test_scenario_round_trips_story_faults_and_step_match(client):
    process, artifact = _make_artifact(client)
    payload = _scenario_payload(
        faults={"CRM API": [{"type": "dependency_unavailable", "probability": 1.0, "config": {}}]},
        step_match="in_order",
        story={"title": "CRM down", "given": "g", "when": "w", "then": "t", "covers": ["sys:CRM API"],
               "source": "suggested", "system_behaviour": {"CRM API": "down"}},
    )
    r = client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=payload)
    assert r.status_code == 200

    listed = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios").json()[0]
    assert listed["step_match"] == "in_order"
    assert listed["faults"]["CRM API"][0]["type"] == "dependency_unavailable"
    assert listed["story"]["title"] == "CRM down"
    assert listed["story"]["system_behaviour"] == {"CRM API": "down"}


def test_scenario_without_new_fields_keeps_defaults(client):
    process, artifact = _make_artifact(client)
    client.post(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios", json=_scenario_payload())
    listed = client.get(f"/api/processes/{process['id']}/agent-artifacts/{artifact['id']}/scenarios").json()[0]
    assert listed["step_match"] == "exact"
    assert listed["faults"] == {}
    assert listed["story"] is None
