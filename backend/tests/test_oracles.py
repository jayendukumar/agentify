"""Epic 33: scalable oracles."""

import json

import pytest

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.nodes import extract_flow_nodes
from app.oracles.rules import RuleSyntaxError, RuleView, evaluate, render, run_oracle, validate_expression
from app.oracles.service import perturb, relation_holds
from app.schemas.common import ProcessElement, ProcessFlow, ProcessSchema
from tests.test_api_blueprint import _finalize, _llm_json
from tests.test_api_twin import _make_artifact


def test_rule_language_evaluates_typed_values():
    rule = {"all": [{"field": "amount", "op": "gt", "value": 10000}, {"field": "vat_id", "op": "missing"}]}
    validate_expression(rule, {"amount", "vat_id"})
    assert evaluate(rule, {"amount": "EUR 12,500", "vat_id": ""}) is True
    assert evaluate(rule, {"amount": 9000}) is False
    assert evaluate(rule, {"amount": 12000, "vat_id": "DE123"}) is False
    assert evaluate({"field": "country", "op": "in", "value": ["DE", "fr"]}, {"country": "FR"}) is True
    assert evaluate({"not": {"field": "tier", "op": "eq", "value": "gold"}}, {"tier": "Gold"}) is False
    assert evaluate({"field": "amount", "op": "eq", "value": 10000}, {"amount": "10,000"}) is True
    assert render(rule) == "amount > 10000 AND vat_id IS MISSING"
    for bad in [{"field": "amount", "op": "approx", "value": 1}, {"all": []}, {"field": "x", "op": "in", "value": 3}, "amount > 3"]:
        with pytest.raises(RuleSyntaxError):
            validate_expression(bad)
    with pytest.raises(RuleSyntaxError, match="Unknown field"):
        validate_expression({"field": "secret", "op": "present"}, {"amount"})


def test_oracle_takes_first_matching_branch_and_most_severe_outcome():
    def rule(id_, gateway, expr, outcome, priority=10, default=False):
        return RuleView(id=id_, gateway_id=gateway, branch_target_id=id_, expression=expr, outcome=outcome,
                        priority=priority, is_default=default, status="confirmed")

    rules = [
        rule("big", "g1", {"field": "amount", "op": "gt", "value": 10000}, "escalate", 10),
        rule("ok", "g1", None, "approve", 99, default=True),
        rule("sanctioned", "g2", {"field": "sanctions_hit", "op": "eq", "value": True}, "reject"),
    ]
    assert run_oracle(rules, {"amount": 500}).decision == "approve"
    assert run_oracle(rules, {"amount": 50000}).decision == "escalate"
    verdict = run_oracle(rules, {"amount": 50000, "sanctions_hit": True})
    assert verdict.decision == "reject" and set(verdict.fired) == {"big", "sanctioned"}
    assert run_oracle(rules, {"amount": 500}).undetermined == ["g2"]


def test_metamorphic_helpers():
    assert perturb("Acme Ltd", {"type": "uppercase"}) == "ACME LTD"
    assert perturb("1,000", {"type": "scale", "factor": 20}) == 20000
    assert perturb(100, {"type": "add", "amount": 0.5}) == 100.5

    class Rel:
        kind = "invariance"
        direction = None

    assert relation_holds(Rel, "approve", "approve") is True and relation_holds(Rel, "approve", "reject") is False
    Rel.kind, Rel.direction = "directional", "toward_escalate"
    assert relation_holds(Rel, "approve", "escalate") is True
    assert relation_holds(Rel, "reject", "escalate") is True
    assert relation_holds(Rel, "approve", "approve") is False
    assert relation_holds(Rel, None, "approve") is None


def _gateway_process(client):
    process, artifact = _make_artifact(client)
    schema = ProcessSchema(
        process_name="Refunds",
        elements=[
            ProcessElement(id="s", type="start_event", label="Start", source_refs=[], confidence="high"),
            ProcessElement(id="a", type="task", label="Check refund", source_refs=[], confidence="high"),
            ProcessElement(id="d", type="decision", label="Amount check", source_refs=[], confidence="high"),
            ProcessElement(id="m", type="task", label="Manager review", source_refs=[], confidence="high"),
            ProcessElement(id="p", type="task", label="Pay refund", source_refs=[], confidence="high"),
            ProcessElement(id="e", type="end_event", label="End", source_refs=[], confidence="high"),
        ],
        flows=[
            ProcessFlow(id="f1", **{"from": "s"}, to="a"),
            ProcessFlow(id="f2", **{"from": "a"}, to="d"),
            ProcessFlow(id="f3", **{"from": "d"}, to="m", condition="amount over 500 EUR"),
            ProcessFlow(id="f4", **{"from": "d"}, to="p", condition="otherwise"),
            ProcessFlow(id="f5", **{"from": "m"}, to="e"),
            ProcessFlow(id="f6", **{"from": "p"}, to="e"),
        ],
    )
    xml, _ = build_bpmn_xml(process["id"], schema)
    version = _finalize(client, process["id"], xml)
    assert "id" in version, version
    gateway = next(n for n in extract_flow_nodes(xml) if n.bpmn_type == "exclusiveGateway")
    return process, artifact, gateway


def _rules_url(process_id, path=""):
    return f"/api/processes/{process_id}/decision-rules{path}"


def _confirmed_rules(client, fake_llm, process, gateway):
    review, pay = [s.node_id for s in gateway.successors if "Manager" in s.label], [s.node_id for s in gateway.successors if "Pay" in s.label]
    fake_llm.complete.return_value = _llm_json(
        {
            "rules": [
                {"branch_target_id": review[0], "expression": {"field": "record_id", "op": "contains", "value": "BIG"}, "outcome": "escalate"},
                {"branch_target_id": pay[0], "expression": None, "outcome": "approve", "is_default": True},
            ]
        }
    )
    rules = client.post(_rules_url(process["id"], "/propose")).json()
    prompt = fake_llm.complete.call_args.args[0][0].content
    assert 'condition: "amount over 500 EUR"' in prompt and "record_id" in prompt
    for rule in rules:
        confirmed = client.patch(_rules_url(process["id"], f"/{rule['id']}"), json={"confirm": True}).json()
        assert confirmed["status"] == "confirmed"
    return rules


def test_propose_validate_and_confirm_rules(client, fake_llm):
    process, artifact, gateway = _gateway_process(client)
    target = gateway.successors[0].node_id
    fake_llm.complete.return_value = _llm_json(
        {"rules": [{"branch_target_id": target, "expression": {"field": "made_up_field", "op": "present"}, "outcome": "escalate"}]}
    )
    rules = client.post(_rules_url(process["id"], "/propose")).json()
    assert len(rules) == 2  # every branch gets a row
    bad = next(r for r in rules if r["branch_target_id"] == target)
    assert bad["status"] == "unstructurable" and "Unknown field" in bad["note"]
    assert bad["condition_text"] in ("amount over 500 EUR", "otherwise")
    refused = client.patch(_rules_url(process["id"], f"/{bad['id']}"), json={"confirm": True})
    assert refused.status_code == 400

    fixed = client.patch(
        _rules_url(process["id"], f"/{bad['id']}"),
        json={"expression": {"field": "record_id", "op": "contains", "value": "BIG"}, "confirm": True},
    ).json()
    assert fixed["status"] == "confirmed" and fixed["rendered"] == "record_id CONTAINS 'BIG'" and fixed["version"] == 2
    # Proposing again leaves decided branches alone.
    assert len(client.post(_rules_url(process["id"], "/propose")).json()) == 2


def _dataset(client, process_id, artifact_id, rows):
    dataset = client.post(f"/api/processes/{process_id}/datasets", json={"name": "Golden", "agent_artifact_id": artifact_id}).json()
    client.post(f"/api/processes/{process_id}/datasets/{dataset['id']}/rows", json=rows)
    return dataset


def test_oracle_labels_flags_conflicts_and_reports_gap(client, fake_llm):
    process, artifact, gateway = _gateway_process(client)
    _confirmed_rules(client, fake_llm, process, gateway)
    dataset = _dataset(
        client, process["id"], artifact["id"],
        [
            {"row_key": "small", "inputs": {"record_id": "r-1"}},
            {"row_key": "big", "inputs": {"record_id": "BIG-9"}},
            {"row_key": "disputed", "inputs": {"record_id": "BIG-2"}, "labels": {"decision": "approve"}, "provenance": "historical"},
            {"row_key": "agrees", "inputs": {"record_id": "r-3"}, "labels": {"decision": "approve"}},
        ],
    )
    base = f"/api/processes/{process['id']}/datasets/{dataset['id']}"
    result = client.post(f"{base}/oracle/label").json()
    assert (result["labelled"], result["agreed"], result["conflicts"]) == (2, 1, 1)
    rows = {r["row_key"]: r for r in client.get(f"{base}/rows").json()["rows"]}
    assert rows["big"]["labels"]["decision"] == "escalate" and rows["big"]["provenance"] == "oracle"
    assert rows["disputed"]["labels"]["decision"] == "approve"  # never overwritten

    conflicts = client.get(f"{base}/oracle/conflicts").json()
    assert [(c["row_key"], c["label"], c["oracle"]) for c in conflicts] == [("disputed", "approve", "escalate")]
    gap = client.post(f"{base}/oracle/conflicts/report-gap", json={"row_keys": ["disputed"]}).json()
    findings = client.get(f"/api/processes/{process['id']}/gap-findings").json()
    finding = next(f for f in findings if f["id"] == gap["gap_finding_id"])
    assert "decided differently from what the documented rules at Amount check say" in finding["question"]

    queue = client.get(f"{base}/review-queue").json()
    assert queue[0]["row_key"] == "disputed" and "label says approve, rules say escalate" in queue[0]["disagreements"]


def test_label_first_generation_keeps_only_oracle_confirmed(client, fake_llm):
    process, artifact, gateway = _gateway_process(client)
    _confirmed_rules(client, fake_llm, process, gateway)
    dataset = _dataset(client, process["id"], artifact["id"], [])
    fake_llm.complete.side_effect = [
        # approve batch
        _llm_json({"records": [{"label": "a", "record": {"record_id": "a-1"}}, {"label": "b", "record": {"record_id": "a-2"}}]}),
        # escalate batch: one record the oracle agrees with, one it doesn't -- so a second batch tops it up
        _llm_json({"records": [{"label": "big", "record": {"record_id": "BIG-1"}}, {"label": "oops", "record": {"record_id": "small"}}]}),
        _llm_json({"records": [{"label": "big", "record": {"record_id": "BIG-2"}}]}),
    ]
    started = client.post(
        f"/api/processes/{process['id']}/datasets/{dataset['id']}/generate",
        json={"total": 4, "mix": {"approve": 0.5, "escalate": 0.5}},
    ).json()
    assert started["queued"] is False
    result = started["result"]
    assert result["kept"] == 4 and result["discarded"] == 1 and result["by_decision"] == {"escalate": 2, "approve": 2}
    rows = client.get(f"/api/processes/{process['id']}/datasets/{dataset['id']}/rows").json()["rows"]
    assert {r["labels"]["decision"] for r in rows} == {"approve", "escalate"} and all(r["provenance"] == "synthetic" for r in rows)


def test_metamorphic_variants(client):
    process, artifact = _make_artifact(client)
    dataset = _dataset(client, process["id"], artifact["id"], [
        {"row_key": "r1", "inputs": {"supplier": "Acme Ltd", "amount": 100}, "labels": {"decision": "approve"}},
        {"row_key": "r2", "inputs": {"supplier": "Globex", "amount": 200}, "labels": {"decision": "reject"}},
    ])
    base = f"/api/processes/{process['id']}/datasets/{dataset['id']}"
    relation = client.post(f"{base}/relations", json={"name": "Name casing", "kind": "invariance", "field": "supplier", "perturbation": {"type": "uppercase"}}).json()
    assert client.post(f"{base}/relations/{relation['id']}/variants").json()["variants"] == 2
    variants = [r for r in client.get(f"{base}/rows").json()["rows"] if any(t.startswith("variant-of:") for t in r["tags"])]
    assert {v["inputs"]["supplier"] for v in variants} == {"ACME LTD", "GLOBEX"}
    assert {v["labels"]["decision"] for v in variants} == {"approve", "reject"}  # invariance keeps the label
    bad = client.post(f"{base}/relations", json={"name": "x", "kind": "directional", "field": "amount", "perturbation": {"type": "scale", "factor": 100}})
    assert bad.status_code == 400


def test_judge_calibration_gate(client, fake_llm):
    from app.db.models import SimulationRunModel
    from app.db.session import get_session_factory
    from app.ids import new_id

    process, artifact = _make_artifact(client)
    rubric = client.post(
        f"/api/processes/{process['id']}/judge-rubrics",
        json={"name": "Clear refusal", "field": "explanation", "rubric": "The explanation names the rule that led to rejection.", "min_samples": 5, "min_agreement": 0.8},
    ).json()
    key = f"judgment:{rubric['id']}"
    rows = [{"row_key": f"r{i}", "inputs": {"i": i}, "labels": {"outputs": {key: i % 2 == 0}}} for i in range(6)]
    dataset = _dataset(client, process["id"], artifact["id"], rows)
    session = get_session_factory()()
    try:
        for i in range(6):
            session.add(SimulationRunModel(
                id=new_id("run"), kind="twin", process_id=process["id"], scenario_id="s", agent_artifact_id=artifact["id"],
                status="COMPLETED", final_output={"explanation": f"text {i}"}, dataset_id=dataset["id"], dataset_version=2, row_key=f"r{i}",
            ))
        session.commit()
    finally:
        session.close()
    # The judge agrees with people on 5 of 6.
    verdicts = [True, False, True, False, True, True]
    fake_llm.complete.side_effect = [_llm_json({"passed": v, "reason": "x", "confidence": 0.9}) for v in verdicts]
    calibrated = client.post(f"/api/processes/{process['id']}/judge-rubrics/{rubric['id']}/calibrate/{dataset['id']}").json()
    assert calibrated["calibration"]["samples"] == 6 and calibrated["calibration"]["agreement"] == 0.833
    assert calibrated["gate_eligible"] is True

    changed = client.put(
        f"/api/processes/{process['id']}/judge-rubrics/{rubric['id']}",
        json={"name": "Clear refusal", "field": "explanation", "rubric": "Stricter: names the rule and the threshold.", "min_samples": 5, "min_agreement": 0.8},
    ).json()
    assert changed["version"] == 2 and changed["gate_eligible"] is False  # recalibrate after any change
