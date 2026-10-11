"""Epic 30: compliance-aware testing and release."""

from datetime import datetime, timezone

from app.db.models import SimulationRunModel
from app.db.session import get_session_factory
from app.ids import new_id
from app.schemas.run import RunStep
from app.schemas.twin import ComplianceAssertion
from app.twin.compliance import check_assertion
from tests.test_api_blueprint import _finalize, _llm_json, _node_result


def _step(seq, type_, target=None, arguments=None):
    return RunStep(seq=seq, type=type_, target=target, arguments=arguments or {}, occurred_at=datetime.now(timezone.utc))


def test_compliance_assertions():
    escalated = ComplianceAssertion(control_id="ctl:x", kind="escalated", role="MLRO")
    asked = [_step(1, "HUMAN_APPROVAL_REQUESTED", arguments={"summary": "Potential match -- MLRO please review"})]
    assert check_assertion(escalated, asked, {}, "COMPLETED") is None
    assert "not to the MLRO" in check_assertion(escalated, [_step(1, "HUMAN_APPROVAL_REQUESTED", arguments={"summary": "ok?"})], {}, "COMPLETED")
    assert check_assertion(escalated.model_copy(update={"role": None}), [], {}, "ESCALATED") is None
    assert "handled it alone" in check_assertion(escalated, [], {"status": "approved"}, "COMPLETED")

    blocked = ComplianceAssertion(control_id="ctl:x", kind="blocked", system="Core banking")
    assert check_assertion(blocked, [_step(1, "TOOL_EXECUTED", "CRM")], {}, "COMPLETED") is None
    assert "carried it out" in check_assertion(blocked, [_step(1, "TOOL_EXECUTED", "Core banking")], {}, "COMPLETED")

    reviewed = ComplianceAssertion(control_id="ctl:x", kind="reviewed", system="Core banking")
    good = [_step(1, "HUMAN_APPROVAL_REQUESTED"), _step(2, "TOOL_EXECUTED", "Core banking")]
    bad = [_step(1, "TOOL_EXECUTED", "Core banking"), _step(2, "HUMAN_APPROVAL_REQUESTED")]
    assert check_assertion(reviewed, good, {}, "COMPLETED") is None
    assert "before a person reviewed" in check_assertion(reviewed, bad, {}, "COMPLETED")

    logged = ComplianceAssertion(control_id="ctl:x", kind="logged")
    assert check_assertion(logged, [], {"decision": "approve", "reason": "within limit"}, "COMPLETED") is None
    assert "logged with an explanation" in check_assertion(logged, [], {"decision": "approve"}, "COMPLETED")


def _create(client, **body):
    response = client.post("/api/organization/entities", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _agent_with_rule(client, fake_llm, *, testable=True):
    control = _create(
        client,
        kind="control",
        code="AML-4",
        name="Sanctions matches reviewed by MLRO",
        data={"description": "Potential matches go to the MLRO.", "requires_human_review": True, "review_role": "MLRO", "testable": testable},
        applies_to={"jurisdictions": ["EU"]},
    )
    _create(client, kind="role", name="Head of Operations")
    _create(
        client,
        kind="approval_authority",
        name="Head of Operations: credit limit",
        data={"role_name": "Head of Operations", "decision_type": "credit limit", "limit_amount": 50000, "currency": "EUR"},
    )
    process = client.post("/api/processes", json={"name": "Onboarding", "scope": {"jurisdictions": ["DE"]}}).json()
    _finalize(client, process["id"])
    spec = {
        "name": "Screening Agent",
        "purpose": "Screen customers",
        "trigger": "New application",
        "tools_systems_needed": ["Screening service"],
        "required_inputs": [{"name": "credit_amount", "source_or_destination": "application", "format": "number"}],
        "expected_outputs": [{"name": "decision", "source_or_destination": "case", "format": "string"}],
        "human_checkpoint": "review_before_action",
    }
    fake_llm.complete.return_value = _llm_json(
        {
            "nodes": [
                _node_result("Event_start", verdict="not_automatable", agent_spec=None, not_automatable_reason="entry"),
                _node_result("Task_a", verdict="partial", agent_spec=spec, applicable_rules=["C1"]),
                _node_result("Event_end", verdict="not_automatable", agent_spec=None, not_automatable_reason="exit"),
            ]
        }
    )
    client.post(f"/api/processes/{process['id']}/blueprint/generate", json={})
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Task_a/agent-artifact").json()
    return process, artifact, control


def _builder(process_id, artifact_id, path):
    return f"/api/processes/{process_id}/agent-artifacts/{artifact_id}/test-builder{path}"


def test_builder_context_has_rules_limits_and_formats(client, fake_llm):
    process, artifact, control = _agent_with_rule(client, fake_llm)
    context = client.get(_builder(process["id"], artifact["id"], "/context")).json()
    rule_target = f"ctl:{control['id']}"
    assert [r["id"] for r in context["rules"]] == [rule_target]
    assert {"id": rule_target, "kind": "control", "label": "Rule AML-4: Sanctions matches reviewed by MLRO"} in context["coverage_targets"]
    assert context["approval_authorities"][0]["limit_amount"] == 50000
    assert context["jurisdictions"] == ["DE"]

    # US30.3: reviewers straight from the approval matrix.
    personas = client.get(_builder(process["id"], artifact["id"], "/authority-personas")).json()["personas"]
    assert personas[0]["name"] == "Head of Operations"
    assert personas[0]["reviewer"] == {"behaviour": "threshold", "field": "credit_amount", "threshold": 50000.0, "persona_name": "Head of Operations"}

    # US30.2: suggestions are asked to cover rules, and assertions are sanitized.
    fake_llm.complete.return_value = _llm_json(
        {
            "situations": [
                {
                    "title": "Near-match on the sanctions list",
                    "covers": [],
                    "inputs": {"credit_amount": 1000},
                    "reviewer": {"behaviour": "reject"},
                    "expect": {
                        "compliance": [
                            {"control": rule_target, "kind": "escalated", "role": "MLRO"},
                            {"control": "ctl:invented", "kind": "escalated"},
                            {"control": rule_target, "kind": "blocked", "system": "Not a system"},
                        ]
                    },
                }
            ]
        }
    )
    situations = client.post(_builder(process["id"], artifact["id"], "/suggest"), json={"count": 1}).json()["situations"]
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert f"{rule_target}: AML-4" in prompt and "Head of Operations: credit limit, up to 50000 EUR" in prompt
    assert "trigger each organization rule" in prompt
    story = situations[0]["story"]
    assert story["covers"] == [rule_target]  # asserting a rule covers it
    assert story["expect"]["compliance"] == [
        {"control_id": rule_target, "kind": "escalated", "role": "MLRO", "system": None, "field": None},
        {"control_id": rule_target, "kind": "blocked", "role": None, "system": None, "field": None},
    ]

    # US30.4: data in the jurisdiction's formats, and near-miss mode.
    fake_llm.complete.return_value = _llm_json({"records": [{"label": "x", "record": {"credit_amount": 50001}}]})
    client.post(_builder(process["id"], artifact["id"], "/data"), json={"mode": "near_miss", "count": 1})
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert "German IBANs" in prompt and "just under and just over each approval limit" in prompt


def test_untestable_controls_are_not_coverage_targets(client, fake_llm):
    process, artifact, _ = _agent_with_rule(client, fake_llm, testable=False)
    context = client.get(_builder(process["id"], artifact["id"], "/context")).json()
    assert context["rules"] == []


def _scenario(client, process_id, artifact_id, rule_target):
    response = client.post(
        f"/api/processes/{process_id}/agent-artifacts/{artifact_id}/scenarios",
        json={
            "name": "Near-match escalates",
            "inputs": {"credit_amount": 1000},
            "story": {"title": "Near-match escalates", "covers": [rule_target], "expect": {"compliance": [{"control_id": rule_target, "kind": "escalated"}]}},
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _record_run(process_id, artifact, scenario_id, *, passed, agent_version=None, pack_id=None):
    session = get_session_factory()()
    try:
        session.add(
            SimulationRunModel(
                id=new_id("run"),
                kind="twin",
                process_id=process_id,
                scenario_id=scenario_id,
                agent_artifact_id=artifact["id"],
                status="COMPLETED",
                graded_passed=passed,
                snapshot={
                    "agent_artifacts": [
                        {
                            "agent_artifact_id": artifact["id"],
                            "agent_version": agent_version or artifact["version"],
                            "context_pack_id": pack_id or artifact["context_pack_id"],
                        }
                    ]
                },
            )
        )
        session.commit()
    finally:
        session.close()


def test_release_gate_and_evidence(client, fake_llm):
    process, artifact, control = _agent_with_rule(client, fake_llm)
    rule_target = f"ctl:{control['id']}"
    evidence_url = f"/api/processes/{process['id']}/blueprint/agent-artifacts/{artifact['id']}/compliance-evidence"
    rc_url = f"/api/processes/{process['id']}/blueprint/agent-artifacts/{artifact['id']}/mark-release-candidate"

    evidence = client.get(evidence_url).json()
    assert evidence["total"] == 1 and evidence["controls"][0]["status"] == "missing"
    assert "not certification" in evidence["disclaimer"]
    refused = client.post(rc_url)
    assert refused.status_code == 400 and "AML-4 (missing)" in refused.json()["detail"]

    scenario = _scenario(client, process["id"], artifact["id"], rule_target)
    _record_run(process["id"], artifact, scenario["id"], passed=False)
    assert client.get(evidence_url).json()["controls"][0]["status"] == "failing"
    _record_run(process["id"], artifact, scenario["id"], passed=True)
    evidence = client.get(evidence_url).json()
    assert evidence["controls"][0]["status"] == "evidenced" and evidence["evidenced"] == 1
    assert {"name": "AML-4", "kind": "control", "version": 1} in evidence["pinned_versions"]

    export = client.get(f"{evidence_url}/export")
    assert export.status_code == 200 and "text/markdown" in export.headers["content-type"]
    assert "**1 of 1 rules evidenced.**" in export.text and "not certification" in export.text

    # A rule change makes the passing evidence out of date (not deleted), and the gate closes again.
    client.put(f"/api/organization/entities/{control['id']}", json={"data": {"description": "Changed.", "requires_human_review": True}})
    evidence = client.get(evidence_url).json()
    assert evidence["context_stale"] is True and evidence["controls"][0]["status"] == "out_of_date"
    assert evidence["controls"][0]["situations"][0]["passed"] is True
    assert client.post(rc_url).status_code == 400
