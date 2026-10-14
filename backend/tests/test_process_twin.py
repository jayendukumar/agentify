"""Epic 36: process digital twin -- sample-data systems (US36.1), case file
hand-off (US36.2), data-driven gateways (US36.3), operating model and
batches (US36.4/US36.5), and the efficiency report (US36.6/US36.7)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.bpmn.nodes import FlowNodeInfo, FlowRef
from app.llm.types import ChatCompletionResult, ToolCall, Usage
from app.oracles.rules import RuleView
from app.process_twin.efficiency import CaseInput, build_report, case_efficiency
from app.schemas.agents import AgentDefinition
from app.schemas.blueprint import AgentIOField
from app.schemas.environment import SimulatedSystemData, TwinEnvironment
from app.schemas.orchestration import GatewayDecision, ManualNodeConfig
from app.schemas.process_twin import NodeOperatingModel, OperatingModel, RoleCost, Triangular
from app.schemas.twin import InferredToolSchema, TwinHumanCheckpointConfig, TwinScenario
from app.twin import orchestrator as orchestrator_module
from app.twin.engine import RunOutcome, run_scenario
from app.twin.orchestrator import NodeArtifact, match_branch, run_orchestration
from app.twin.records import RecordsSession, find_system_data, records_tool_schema

from .test_api_orchestration import _make_process_with_manual_node, _scenario_payload
from .test_orchestrator import _definition, _scenario

NOW = datetime(2026, 10, 13, tzinfo=timezone.utc)


def _environment(**system_data) -> TwinEnvironment:
    return TwinEnvironment(
        id="env_1", process_id="proc_1", name="Sample data", version=1, created_at=NOW, updated_at=NOW,
        system_data=system_data,
    )


VENDORS = SimulatedSystemData(
    description="Vendor master",
    aliases=["Supplier master data (SAP MDG)"],
    records=[
        {"supplier_id": "SUP-001", "name": "Acme GmbH", "vat_number": "DE123", "status": "active"},
        {"supplier_id": "SUP-002", "name": "Brightline BV", "vat_number": "NL456", "status": "blocked"},
    ],
    lookup_fields=["supplier_id", "vat_number", "name"],
    operations=["lookup", "update"],
    reference_prefix="VEN",
)


# -- US36.1: sample-data systems -----------------------------------------------


def test_find_system_data_by_name_alias_and_words():
    env = _environment(**{"Vendor Master": VENDORS})
    assert find_system_data(env, "Vendor Master") is VENDORS
    assert find_system_data(env, "supplier master data (sap mdg)") is VENDORS
    assert find_system_data(env, "Vendor Master (SAP)") is VENDORS
    assert find_system_data(env, "Payments") is None
    assert find_system_data(None, "Vendor Master") is None


def test_records_lookup_matches_all_given_fields_case_insensitively():
    session = RecordsSession()
    found = session.call(VENDORS, "Vendor Master", {"action": "lookup", "vat_number": "de123"})
    assert found["found"] is True and found["records"][0]["supplier_id"] == "SUP-001"
    none = session.call(VENDORS, "Vendor Master", {"action": "lookup", "vat_number": "DE123", "name": "Brightline BV"})
    assert none["found"] is False
    # An argument named differently is matched against the lookup fields' values.
    renamed = session.call(VENDORS, "Vendor Master", {"action": "lookup", "vendor": "SUP-002"})
    assert renamed["records"][0]["name"] == "Brightline BV"


def test_records_writes_are_seen_by_later_lookups_in_the_same_run_only():
    session = RecordsSession()
    result = session.call(VENDORS, "Vendor Master", {"action": "update", "supplier_id": "SUP-002", "data": {"status": "active"}})
    assert result["status"] == "accepted" and result["reference"] == "VEN-00001"
    assert session.call(VENDORS, "Vendor Master", {"action": "lookup", "supplier_id": "SUP-002"})["records"][0]["status"] == "active"
    assert VENDORS.records[1]["status"] == "blocked"  # the environment itself is untouched
    assert RecordsSession().call(VENDORS, "Vendor Master", {"action": "lookup", "supplier_id": "SUP-002"})["records"][0]["status"] == "blocked"
    assert session.call(VENDORS, "Vendor Master", {"action": "create", "data": {}})["error"] == "operation_not_supported"


def test_records_tool_schema_is_built_from_the_data():
    schema = records_tool_schema("Vendor Master (SAP)", VENDORS)
    assert schema.source == "records"
    assert schema.tool_name == "vendor_master_sap_records"
    assert schema.parameters["properties"]["action"]["enum"] == ["lookup", "update"]
    assert {"supplier_id", "vat_number", "name", "data"} <= set(schema.parameters["properties"])


def _tool_call_result(name: str, arguments: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=None, tool_calls=[ToolCall(id="call_1", name=name, arguments=arguments)], finish_reason="tool_calls",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model",
    )


def _text_result(payload: dict) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=json.dumps(payload), tool_calls=[], finish_reason="stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model",
    )


@pytest.mark.asyncio
async def test_engine_answers_sample_data_systems_without_an_llm_call(fake_llm):
    definition = _definition(tools_systems_needed=["Vendor Master"], output_schema=[
        AgentIOField(name="supplier_status", format="string", source_or_destination="case")
    ])
    inferred = InferredToolSchema(
        system_name="Vendor Master", tool_name="get_vendor", description="x", parameters={"type": "object"},
        response_shape_description="x",
    )
    fake_llm.complete = AsyncMock(side_effect=[
        _tool_call_result("vendor_master_records", {"action": "lookup", "supplier_id": "SUP-002"}),
        _text_result({"supplier_status": "blocked"}),
    ])
    scenario = TwinScenario(id="s", agent_artifact_id="a", name="n", inputs={"supplier_id": "SUP-002"},
                            expected_steps=[], expected_outputs={}, created_at=NOW)
    outcome = await run_scenario(
        fake_llm, artifact=definition, tool_schemas={"Vendor Master": inferred}, scenario=scenario, max_turns=4,
        environment=_environment(**{"Vendor Master": VENDORS}),
    )
    assert outcome.status == "COMPLETED"
    executed = [s for s in outcome.steps if s.type == "TOOL_EXECUTED"]
    assert executed[0].result["records"][0]["status"] == "blocked"
    assert fake_llm.complete.await_count == 2  # the two agent turns; no system simulation call
    offered = fake_llm.complete.await_args_list[0].kwargs["tools"]
    assert [t.name for t in offered] == ["vendor_master_records"]


# -- US36.2/US36.3: case file and data-driven gateways -------------------------


def _ref(node_id: str, condition: str | None = None) -> FlowRef:
    # As app/bpmn/nodes.py builds it: `label` is the target's label, the
    # branch wording is the flow's condition.
    return FlowRef(node_id=node_id, label=node_id.title(), condition=condition)


def _gateway_flow() -> list[FlowNodeInfo]:
    """start -> capture (agent) -> match (agent) -> G "Matched?" -yes-> pay (agent) -> end_paid
                                                              -no--> review (manual) -> end_held"""
    return [
        FlowNodeInfo(id="start", label="Start", bpmn_type="startEvent", lane_name=None, successors=[_ref("capture")]),
        FlowNodeInfo(id="capture", label="Capture", bpmn_type="task", lane_name="AP", successors=[_ref("match")]),
        FlowNodeInfo(id="match", label="Match", bpmn_type="task", lane_name="AP", successors=[_ref("g")]),
        FlowNodeInfo(id="g", label="Matched within tolerance?", bpmn_type="exclusiveGateway", lane_name=None,
                     successors=[_ref("pay", "Yes"), _ref("review", "No - exception")]),
        FlowNodeInfo(id="pay", label="Pay", bpmn_type="task", lane_name="AP", successors=[_ref("end_paid")]),
        FlowNodeInfo(id="review", label="Review", bpmn_type="userTask", lane_name="AP clerk", successors=[_ref("end_held")]),
        FlowNodeInfo(id="end_paid", label="Paid", bpmn_type="endEvent", lane_name=None),
        FlowNodeInfo(id="end_held", label="Held", bpmn_type="endEvent", lane_name=None),
    ]


def _artifacts(**input_fields: list[str]) -> dict[str, NodeArtifact]:
    return {
        node: NodeArtifact(artifact_id=f"art_{node}", tool_schemas={}, definition=_definition(input_schema=[
            AgentIOField(name=f, format="string", source_or_destination="case") for f in fields
        ]))
        for node, fields in input_fields.items()
    }


def test_match_branch_is_forgiving_but_never_guesses():
    options = {"Yes": "pay", "No - exception": "review"}
    assert match_branch("yes", options) == "pay"
    assert match_branch("No", options) == "review"
    assert match_branch("No -- Exception!", options) == "review"
    assert match_branch("maybe", options) is None
    assert match_branch(None, options) is None


@pytest.mark.asyncio
async def test_case_file_hands_earlier_facts_forward_and_agent_routes_the_gateway(fake_llm, monkeypatch):
    outputs = {
        "capture": {"invoice_id": "INV-1", "amount": 120.0, "due_date": "2026-11-01"},
        "match": {"match_status": "variance", "route": "No"},
    }
    calls = []

    async def fake_run_scenario(llm, *, scenario, node_id, routing=None, **kwargs):
        calls.append((node_id, dict(scenario.inputs), routing))
        return RunOutcome(status="COMPLETED", final_output=outputs[node_id], total_cost_usd=0.001, total_tokens=3)

    monkeypatch.setattr(orchestrator_module, "run_scenario", fake_run_scenario)
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), max_turns_per_node=4,
        node_artifacts=_artifacts(capture=["document_ref"], match=["invoice_id", "amount"], pay=["due_date"]),
        scenario=_scenario(inputs={"document_ref": "DOC-9"}, handoff="case_file", default_gateway_routing="agent"),
    )

    assert outcome.status == "COMPLETED"
    assert outcome.visited_path == ["start", "capture", "match", "g", "review", "end_held"]
    # match got capture's fields copied from the case file; no adapter LLM call anywhere.
    assert calls[1] == ("match", {"invoice_id": "INV-1", "amount": 120.0,
                                  "case_file": {"document_ref": "DOC-9", "due_date": "2026-11-01"}},
                        ("Matched within tolerance?", ["Yes", "No - exception"]))
    assert calls[0][2] is None  # capture's successor is a task, not a gateway
    fake_llm.complete.assert_not_called()
    decision = next(s for s in outcome.steps if s.type == "DECISION")
    assert decision.result == {"to_node_id": "review", "mode": "agent", "agent_choice": "No"}
    delegated = next(s for s in outcome.steps if s.type == "AGENT_DELEGATED" and s.node_id == "match")
    assert delegated.result["copied"] == ["invoice_id", "amount"] and delegated.result["adapted"] == []
    # The final output is the whole case file, without the consumed route.
    assert outcome.final_output["due_date"] == "2026-11-01"
    assert outcome.final_output["human_decision"] == "approve"
    assert "route" not in outcome.final_output


@pytest.mark.asyncio
async def test_auto_mapping_only_adapts_the_missing_fields(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=RunOutcome(
        status="COMPLETED", final_output={"route": "Yes"}, total_cost_usd=0.0, total_tokens=0)))
    fake_llm.complete = AsyncMock(return_value=_text_result({"po_number": "PO-7"}))
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), max_turns_per_node=4,
        node_artifacts=_artifacts(capture=[], match=["invoice_id", "po_number"], pay=[]),
        scenario=_scenario(inputs={"invoice_id": "INV-1"}, handoff="case_file", default_gateway_routing="agent"),
    )
    assert outcome.status == "COMPLETED"
    prompt = fake_llm.complete.await_args.args[0][0].content
    assert "po_number" in prompt and "invoice_id (" not in prompt  # only the missing field was asked for
    delegated = next(s for s in outcome.steps if s.type == "AGENT_DELEGATED" and s.node_id == "match")
    assert {k: v for k, v in delegated.result["input_after"].items() if k != "case_file"} == {
        "invoice_id": "INV-1", "po_number": "PO-7"}
    assert delegated.result["adapted"] == ["po_number"]


def _rule(rule_id, target, expression=None, *, is_default=False, priority=100) -> RuleView:
    return RuleView(id=rule_id, gateway_id="g", branch_target_id=target, expression=expression,
                    is_default=is_default, priority=priority, status="confirmed")


@pytest.mark.asyncio
async def test_rules_route_on_the_case_file_and_fall_back_to_the_default_rule(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=RunOutcome(
        status="COMPLETED", final_output={}, total_cost_usd=0.0, total_tokens=0)))
    rules = [
        _rule("r_big", "review", {"field": "amount", "op": "gt", "value": 10000}, priority=1),
        _rule("r_default", "pay", is_default=True),
    ]
    nodes = _artifacts(capture=[], match=[], pay=[])
    big = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), node_artifacts=nodes, max_turns_per_node=4, decision_rules=rules,
        scenario=_scenario(inputs={"amount": "EUR 12,500"}, handoff="case_file", default_gateway_routing="rules"),
    )
    small = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), node_artifacts=nodes, max_turns_per_node=4, decision_rules=rules,
        scenario=_scenario(inputs={"amount": 900}, handoff="case_file", default_gateway_routing="rules"),
    )
    assert big.visited_path[-2:] == ["review", "end_held"]
    assert small.visited_path[-2:] == ["pay", "end_paid"]
    decision = next(s for s in big.steps if s.type == "DECISION")
    assert decision.result["rule_id"] == "r_big" and decision.result["mode"] == "rules"


@pytest.mark.asyncio
async def test_unresolvable_agent_route_falls_back_to_configured_or_fails_readably(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=RunOutcome(
        status="COMPLETED", final_output={"route": "perhaps"}, total_cost_usd=0.0, total_tokens=0)))
    nodes = _artifacts(capture=[], match=[], pay=[])
    failed = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), node_artifacts=nodes, max_turns_per_node=4,
        scenario=_scenario(handoff="case_file", default_gateway_routing="agent"),
    )
    assert failed.status == "FAILED"
    assert "'perhaps' is not one of" in failed.deviations[0].reason
    fallback = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), node_artifacts=nodes, max_turns_per_node=4,
        scenario=_scenario(handoff="case_file", default_gateway_routing="agent",
                           gateway_decisions={"g": GatewayDecision(to_node_id="pay")}),
    )
    assert fallback.status == "COMPLETED" and "pay" in fallback.visited_path
    decision = next(s for s in fallback.steps if s.type == "DECISION")
    assert decision.result["used_configured_decision"] is True


@pytest.mark.asyncio
async def test_case_seed_makes_probabilistic_reviewers_reproducible(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=RunOutcome(
        status="COMPLETED", final_output={"route": "No"}, total_cost_usd=0.0, total_tokens=0)))
    scenario = _scenario(handoff="case_file", default_gateway_routing="agent", manual_node_config={
        "review": ManualNodeConfig(human_checkpoint_config=TwinHumanCheckpointConfig(approve_probability=0.5)),
    })
    nodes = _artifacts(capture=[], match=[], pay=[])

    async def decision_for(seed):
        out = await run_orchestration(fake_llm, flow_nodes=_gateway_flow(), node_artifacts=nodes,
                                      max_turns_per_node=4, scenario=scenario, seed=seed)
        return out.final_output["decision_review"]

    decisions = [await decision_for(s) for s in range(12)]
    assert decisions == [await decision_for(s) for s in range(12)]
    assert {"approve", "reject"} == set(decisions)


# -- US36.6/US36.7: efficiency ------------------------------------------------


def _model() -> OperatingModel:
    fixed = lambda m: Triangular(min=m, likely=m, max=m)  # noqa: E731
    return OperatingModel(
        monthly_volume=1000, productive_hours_per_fte=1600, roles=[RoleCost(role="AP", hourly_cost=60)],
        nodes=[
            NodeOperatingModel(node_id="capture", role="AP", handling_minutes=fixed(6), wait_minutes=fixed(60)),
            NodeOperatingModel(node_id="match", role="AP", handling_minutes=fixed(10), wait_minutes=fixed(120),
                               review_minutes=fixed(3)),
            NodeOperatingModel(node_id="review", role="AP", handling_minutes=fixed(20), wait_minutes=fixed(240),
                               to_be_wait_minutes=fixed(30)),
        ],
        end_event_outcomes={"end_paid": "approve", "end_held": "escalate"},
        usd_to_currency=1.0,
    )


def _node_run(node_id, kind, seconds=0.0, status="COMPLETED"):
    return {"node_id": node_id, "kind": kind, "status": status, "started_at": NOW.isoformat(),
            "completed_at": (NOW + timedelta(seconds=seconds)).isoformat(), "total_cost_usd": 0.002}


def _case(run_id, *, reviewed=False, held=False, status="COMPLETED", expected="approve") -> CaseInput:
    runs = [_node_run("capture", "agent", 30), _node_run("match", "agent", 60)]
    if held:
        runs.append(_node_run("review", "manual"))
    runs.append({"node_id": "end_held" if held else "end_paid", "kind": "end_event", "status": "COMPLETED"})
    steps = [{"type": "HUMAN_APPROVAL_REQUESTED", "node_id": "match"}] if reviewed else []
    return CaseInput(run_id=run_id, row_key=run_id, status=status, node_runs=runs, steps=steps,
                     visited_path=[r["node_id"] for r in runs], total_cost_usd=0.004,
                     labels={"decision": expected}, seed=1, deviations=[])


def test_case_efficiency_costs_both_sides_of_the_same_path():
    straight = case_efficiency(_case("c1"), _model())
    assert straight.as_is_cycle_minutes == 196  # (60+6) + (120+10)
    assert straight.as_is_touch_minutes == 16
    assert straight.as_is_cost == 16.0
    assert straight.to_be_cycle_minutes == 1.5  # 90 agent seconds
    assert straight.to_be_touch_minutes == 0 and straight.straight_through is True
    assert straight.outcome == "approve" and straight.correct is True

    held = case_efficiency(_case("c2", reviewed=True, held=True, expected="escalate"), _model())
    # agents 1.5 + review (wait 120 + 3) + manual review (to-be wait 30 + 20)
    assert held.to_be_cycle_minutes == pytest.approx(174.5)
    assert held.to_be_touch_minutes == 23 and held.human_touches == 2
    assert held.as_is_cycle_minutes == 456  # 196 + 240 + 20
    assert held.outcome == "escalate" and held.correct is True


def test_a_failed_case_falls_back_to_full_manual_handling():
    failed = case_efficiency(_case("c3", status="FAILED"), _model())
    assert failed.straight_through is False and failed.outcome is None and failed.correct is False
    assert failed.to_be_touch_minutes >= failed.as_is_touch_minutes


def test_report_summarises_straight_through_accuracy_and_annual_savings():
    labels = {"capture": ("Capture", "task"), "match": ("Match", "task"), "review": ("Review", "userTask"),
              "end_paid": ("Paid", "endEvent"), "end_held": ("Held", "endEvent")}
    cases = [_case(f"ok{i}") for i in range(8)] + [_case("held", reviewed=True, held=True, expected="escalate"),
                                                  _case("wrong", expected="reject")]
    report = build_report(cases, _model(), labels, model_version=3)
    assert report.cases == 10 and report.completed == 10
    assert report.straight_through.passed == 9
    assert report.accuracy.passed == 9 and report.accuracy.total == 10
    assert report.touch_time_reduction.mean > 0.8
    assert report.annual_fte_released.mean > 0
    assert report.annual_fte_released.low <= report.annual_fte_released.mean <= report.annual_fte_released.high
    assert "(v3)" in report.assumptions[0]
    assert {c.actual for c in report.outcome_matrix if c.expected == "reject"} == {"approve"}
    match = next(n for n in report.nodes if n.node_id == "match")
    assert match.review_rate == pytest.approx(0.1) and match.mean_agent_seconds == 60
    assert [n.node_id for n in report.nodes] == ["capture", "match", "review"]


# -- US36.4/US36.5 through the API --------------------------------------------


def test_operating_model_defaults_from_the_diagram_then_saves_versioned(client):
    process, _ = _make_process_with_manual_node(client)
    base = f"/api/processes/{process['id']}/process-twin"
    default = client.get(f"{base}/operating-model").json()
    assert default["is_default"] is True and default["version"] == 0
    assert [n["node_id"] for n in default["nodes"]] == ["Task_a"]
    assert "Event_end" in default["end_event_outcomes"]

    default["nodes"][0]["handling_minutes"] = {"min": 5, "likely": 8, "max": 15}
    default["roles"] = [{"role": "Clerk", "hourly_cost": 45}]
    saved = client.put(f"{base}/operating-model", json=default).json()
    assert saved["version"] == 1 and saved["is_default"] is False
    bad = dict(default, nodes=[dict(default["nodes"][0], handling_minutes={"min": 9, "likely": 8, "max": 15})])
    assert client.put(f"{base}/operating-model", json=bad).status_code == 422


def test_process_twin_batch_runs_every_case_and_reports(client):
    process, _ = _make_process_with_manual_node(client)
    pid = process["id"]
    base = f"/api/processes/{pid}/process-twin"
    model = client.get(f"{base}/operating-model").json()
    model["nodes"][0].update(role="Clerk", handling_minutes={"min": 10, "likely": 10, "max": 10},
                             wait_minutes={"min": 60, "likely": 60, "max": 60})
    model["roles"] = [{"role": "Clerk", "hourly_cost": 60}]
    model["end_event_outcomes"] = {"Event_end": "approve"}
    client.put(f"{base}/operating-model", json=model)

    dataset = client.post(f"/api/processes/{pid}/datasets", json={"name": "Cases"}).json()
    client.post(f"/api/processes/{pid}/datasets/{dataset['id']}/rows", json=[
        {"row_key": "a", "inputs": {"amount": 10}, "labels": {"decision": "approve"}},
        {"row_key": "b", "inputs": {"amount": 20}, "labels": {"decision": "reject"}},
    ])
    scenario = client.post(f"/api/processes/{pid}/orchestration/scenarios",
                           json=_scenario_payload(handoff="case_file")).json()
    request = {"scenario_id": scenario["id"], "dataset_id": dataset["id"], "seed": 3}

    estimate = client.post(f"{base}/estimate", json=request).json()
    assert estimate["cases"] == 2

    started = client.post(f"{base}/batches", json=request)
    assert started.status_code == 202
    batch = client.get(f"{base}/batches/{started.json()['id']}").json()
    assert batch["status"] == "completed" and batch["finished"] == 2

    report = client.get(f"{base}/batches/{batch['id']}/report").json()
    assert report["cases"] == 2 and report["completed"] == 2
    assert report["accuracy"]["passed"] == 1  # "b" expected reject, the process approved it
    assert report["as_is_touch_minutes"]["mean"] == 10
    by_row = {c["row_key"]: c for c in report["cases_detail"]}
    assert by_row["a"]["to_be_cycle_minutes"] == 70 and by_row["a"]["human_touches"] == 1
    runs = client.get(f"/api/processes/{pid}/orchestration/runs").json()
    assert {r["final_output"]["amount"] for r in runs} == {10, 20}  # each case ran on its own row


def test_process_twin_refuses_scenarios_that_wait_for_a_real_person(client):
    process, _ = _make_process_with_manual_node(client)
    pid = process["id"]
    dataset = client.post(f"/api/processes/{pid}/datasets", json={"name": "Cases"}).json()
    client.post(f"/api/processes/{pid}/datasets/{dataset['id']}/rows", json=[{"row_key": "a", "inputs": {}}])
    scenario = client.post(f"/api/processes/{pid}/orchestration/scenarios", json=_scenario_payload(
        manual_node_config={"Task_a": {"mode": "human_checkpoint", "human_checkpoint_config": {"mode": "manual"}}},
    )).json()
    r = client.post(f"/api/processes/{pid}/process-twin/batches",
                    json={"scenario_id": scenario["id"], "dataset_id": dataset["id"]})
    assert r.status_code == 400 and "real person" in r.json()["detail"]


def test_an_agent_on_an_event_does_not_block_the_walk(client):
    """Seen live: chunked blueprint evaluation gave the end event an agent
    with no output schema; it can never run there, so it mustn't stop the run."""
    from .test_api_agents import _automatable_node, _not_automatable_node, _seed_blueprint
    from .test_api_blueprint import _finalize
    from .test_api_orchestration import _await_terminal_run

    process = client.post("/api/processes", json={"name": "P"}).json()
    version = _finalize(client, process["id"])
    end_agent = _automatable_node("Event_end")
    end_agent.agent_spec.expected_outputs = []  # untyped output: never simulation-ready
    _seed_blueprint(process["id"], version["id"], [_not_automatable_node("Task_a"), end_agent])
    artifact = client.post(f"/api/processes/{process['id']}/blueprint/nodes/Event_end/agent-artifact").json()
    assert artifact["lifecycle_status"] == "DRAFT"
    scenario = client.post(f"/api/processes/{process['id']}/orchestration/scenarios", json=_scenario_payload()).json()
    run = client.post(f"/api/processes/{process['id']}/orchestration/scenarios/{scenario['id']}/run").json()
    assert _await_terminal_run(client, process["id"], run["id"])["status"] == "COMPLETED"


@pytest.mark.asyncio
async def test_agent_steps_are_part_of_the_run_trace(fake_llm, monkeypatch):
    """Epic 17's one-trace promise: before Epic 36 an agent's own steps went
    straight to the persistence callback and were then overwritten by the
    walk's final step list -- the efficiency report undercounted reviews."""
    from app.schemas.run import RunStep

    async def agent(llm, *, node_id, on_step, **kwargs):
        for t in ("MODEL_INVOKED", "HUMAN_APPROVAL_REQUESTED", "TOOL_EXECUTED"):
            await on_step(RunStep(seq=0, type=t, node_id=node_id, occurred_at=NOW))
        return RunOutcome(status="COMPLETED", final_output={"route": "Yes"}, total_cost_usd=0.0, total_tokens=0)

    monkeypatch.setattr(orchestrator_module, "run_scenario", agent)
    persisted = []

    async def on_step(step):
        persisted.append(step)

    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow(), node_artifacts=_artifacts(capture=[], match=[], pay=[]),
        max_turns_per_node=4, scenario=_scenario(handoff="case_file", default_gateway_routing="agent"), on_step=on_step,
    )
    reviews = [s for s in outcome.steps if s.type == "HUMAN_APPROVAL_REQUESTED"]
    assert [s.node_id for s in reviews] == ["capture", "match", "pay"]
    assert [s.seq for s in outcome.steps] == list(range(len(outcome.steps)))
    assert [s.seq for s in persisted] == [s.seq for s in outcome.steps]


# -- targeted at mutants that survived (python -m tools.code_health mutation) --


def test_sampling_edges_are_exact():
    import random as _random

    from app.process_twin.efficiency import _seconds, sample

    rng = _random.Random(1)
    assert sample(None, rng) == 0.0
    assert sample(Triangular(min=4, likely=4, max=4), rng) == 4
    draws = [sample(Triangular(min=1, likely=2, max=9), _random.Random(s)) for s in range(50)]
    assert all(1 <= d <= 9 for d in draws) and len(set(draws)) > 40
    assert _seconds({"started_at": None, "completed_at": NOW.isoformat()}) == 0.0
    assert _seconds({"started_at": NOW.isoformat()}) == 0.0
    later = (NOW + timedelta(seconds=30)).isoformat()
    assert _seconds({"started_at": later, "completed_at": NOW.isoformat()}) == 0.0  # clock skew never goes negative


def test_review_time_is_costed_at_the_role_rate():
    held = case_efficiency(_case("c2", reviewed=True, held=True, expected="escalate"), _model())
    assert held.to_be_labour_cost == 23.0  # (3 review + 20 manual) minutes at EUR 60/h
    assert held.as_is_cost == 36.0  # (6 + 10 + 20) minutes at EUR 60/h


def test_intervals_bracket_the_mean_and_handle_degenerate_input():
    import random as _random

    from app.process_twin.efficiency import _mean_interval, _ratio_interval

    rng = _random.Random(3)
    i = _mean_interval([1.0, 2.0, 3.0, 4.0, 10.0], rng)
    assert i.mean == 4.0 and 1.0 <= i.low < i.mean < i.high <= 10.0
    assert _mean_interval([7.0], rng).model_dump() == {"mean": 7.0, "low": 7.0, "high": 7.0}
    assert _mean_interval([], rng).mean is None
    same = _ratio_interval([10.0, 10.0, 10.0], [4.0, 4.0, 4.0], rng)
    assert same.model_dump() == {"mean": 0.6, "low": 0.6, "high": 0.6}
    spread = _ratio_interval([10.0, 20.0, 30.0, 40.0], [9.0, 4.0, 30.0, 2.0], rng)
    assert spread.low < spread.mean < spread.high
    assert _ratio_interval([0.0], [0.0], rng).mean is None


def test_report_headline_and_manual_step_people_time():
    labels = {"capture": ("Capture", "task"), "match": ("Match", "task"), "review": ("Review", "userTask"),
              "end_paid": ("Paid", "endEvent"), "end_held": ("Held", "endEvent")}
    cases = [_case(f"ok{i}") for i in range(9)] + [_case("held", reviewed=True, held=True, expected="escalate")]
    report = build_report(cases, _model(), labels)
    assert "90% went straight through" in report.headline
    assert "right outcome in 100% of labelled cases" in report.headline
    review = next(n for n in report.nodes if n.node_id == "review")
    assert review.kind == "manual" and review.to_be_touch_minutes == 20 and review.review_rate is None
    capture = next(n for n in report.nodes if n.node_id == "capture")
    assert capture.to_be_touch_minutes == 0  # never asked for review


def test_records_tool_and_lookup_edges():
    env = _environment(**{"Vendor Master": VENDORS})
    assert find_system_data(env, "VENDOR-MASTER") is VENDORS  # same words, other punctuation
    long_name = records_tool_schema("A" * 80, VENDORS).tool_name
    assert len(long_name) <= 64 and long_name.endswith("_records")
    assert records_tool_schema("Vendor Master", VENDORS).description.endswith("or update a record.")
    lookup_only = SimulatedSystemData(records=[{"k": 1}], lookup_fields=["k"])
    assert records_tool_schema("X", lookup_only).description.endswith("any of: k.")
    nothing = RecordsSession().call(VENDORS, "Vendor Master", {"action": "lookup"})
    assert nothing["found"] is False and "supplier_id" in nothing["message"]
    ledger = SimulatedSystemData(records=[], lookup_fields=["invoice_number"], operations=["lookup", "create"],
                                 reference_prefix="AP")
    session = RecordsSession()
    created = session.call(ledger, "AP ledger", {"action": "create", "data": {"invoice_number": "X-1"}})
    assert created == {"status": "accepted", "reference": "AP-00001", "record": {"invoice_number": "X-1"}}
    found = session.call(ledger, "AP ledger", {"action": "lookup", "invoice_number": "x-1"})
    assert found["records"][0]["reference"] == "AP-00001"


@pytest.mark.asyncio
async def test_a_final_answer_that_is_not_json_gets_one_repair_turn(fake_llm):
    definition = _definition(output_schema=[AgentIOField(name="status", format="string", source_or_destination="case")])
    scenario = TwinScenario(id="s", agent_artifact_id="a", name="n", inputs={}, expected_steps=[],
                            expected_outputs={}, created_at=NOW)
    prose = ChatCompletionResult(text="Done! The status is ok.", tool_calls=[], finish_reason="stop",
                                 usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model")
    fake_llm.complete = AsyncMock(side_effect=[prose, _text_result({"status": "ok"})])
    repaired = await run_scenario(fake_llm, artifact=definition, tool_schemas={}, scenario=scenario, max_turns=4)
    assert repaired.status == "COMPLETED" and repaired.final_output == {"status": "ok"}
    assert [s.type for s in repaired.steps].count("RETRY") == 1
    assert "exactly these keys: ['status']" in fake_llm.complete.await_args.args[0][-1].content

    fake_llm.complete = AsyncMock(side_effect=[prose, prose])
    failed = await run_scenario(fake_llm, artifact=definition, tool_schemas={}, scenario=scenario, max_turns=4)
    assert failed.status == "FAILED" and "not a valid JSON object" in failed.deviations[0].reason
