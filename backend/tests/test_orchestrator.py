from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.bpmn.nodes import FlowNodeInfo, FlowRef
from app.llm.types import ChatCompletionResult, Usage
from app.schemas.agents import AgentDefinition
from app.schemas.blueprint import AgentIOField
from app.schemas.orchestration import GatewayDecision, ManualNodeConfig, OrchestrationScenario
from app.schemas.run import TwinDeviation
from app.twin import orchestrator as orchestrator_module
from app.twin.engine import RunCancelled, RunOutcome
from app.twin.orchestrator import NodeArtifact, grade_orchestration, run_orchestration


def _scenario(**overrides) -> OrchestrationScenario:
    defaults = dict(
        id="orchsc_1",
        process_id="proc_1",
        baseline_version_id="v1",
        name="Happy path",
        inputs={"a": 1},
        gateway_decisions={},
        manual_node_config={},
        system_stubs={},
        human_checkpoint_config={},
        data_mapping_mode={},
        expected_path=[],
        expected_final_output={},
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return OrchestrationScenario(**defaults)


def _definition(**overrides) -> AgentDefinition:
    defaults = dict(
        name="Agent",
        purpose="Do the thing",
        trigger="on step",
        system_prompt="You do the thing.",
        input_schema=[],
        output_schema=[],
        tools_systems_needed=[],
        human_checkpoint="none",
        model="test-model",
    )
    defaults.update(overrides)
    return AgentDefinition(**defaults)


def _agent_outcome(**overrides) -> RunOutcome:
    defaults = dict(status="COMPLETED", steps=[], final_output={"result": "ok"}, deviations=[], graded_passed=True, total_cost_usd=0.001, total_tokens=5, turns_used=1)
    defaults.update(overrides)
    return RunOutcome(**defaults)


def _llm_result(*, text: str) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text, tool_calls=[], finish_reason="stop", usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2), model="test-model"
    )


# -- grade_orchestration --------------------------------------------------------


def test_grade_orchestration_passes_with_no_expectations():
    scenario = _scenario()
    graded_passed, deviations = grade_orchestration(["n1", "n2"], {"x": 1}, scenario)
    assert graded_passed is True
    assert deviations == []


def test_grade_orchestration_flags_path_mismatch():
    scenario = _scenario(expected_path=["n1", "n2"])
    graded_passed, deviations = grade_orchestration(["n1", "n3"], {}, scenario)
    assert graded_passed is False
    assert any("n3" in d.reason for d in deviations)


def test_grade_orchestration_flags_final_output_mismatch():
    scenario = _scenario(expected_final_output={"amount": 100})
    graded_passed, deviations = grade_orchestration([], {"amount": 50}, scenario)
    assert graded_passed is False
    assert any("amount" in d.reason for d in deviations)


def test_grade_orchestration_flags_missing_final_output_field():
    scenario = _scenario(expected_final_output={"amount": 100})
    graded_passed, deviations = grade_orchestration([], {}, scenario)
    assert graded_passed is False
    assert any("missing" in d.reason for d in deviations)


# -- run_orchestration: linear happy path ----------------------------------------


def _linear_flow_nodes() -> list[FlowNodeInfo]:
    return [
        FlowNodeInfo(
            id="start", label="Start", bpmn_type="startEvent", lane_name=None,
            predecessors=[], successors=[FlowRef(node_id="task", label="Task", condition=None)],
        ),
        FlowNodeInfo(
            id="task", label="Task", bpmn_type="task", lane_name=None,
            predecessors=[FlowRef(node_id="start", label="Start", condition=None)],
            successors=[FlowRef(node_id="end", label="End", condition=None)],
        ),
        FlowNodeInfo(
            id="end", label="End", bpmn_type="endEvent", lane_name=None,
            predecessors=[FlowRef(node_id="task", label="Task", condition=None)], successors=[],
        ),
    ]


@pytest.mark.asyncio
async def test_run_orchestration_linear_agent_then_end(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=_agent_outcome()))
    flow_nodes = _linear_flow_nodes()
    node_artifacts = {"task": NodeArtifact(artifact_id="art_1", definition=_definition(), tool_schemas={})}

    outcome = await run_orchestration(
        fake_llm, flow_nodes=flow_nodes, node_artifacts=node_artifacts, scenario=_scenario(), max_turns_per_node=8
    )

    assert outcome.status == "COMPLETED"
    assert outcome.visited_path == ["start", "task", "end"]
    assert outcome.final_output == {"result": "ok"}
    assert outcome.total_cost_usd == pytest.approx(0.001)
    assert outcome.total_tokens == 5
    kinds = [nr.kind for nr in outcome.node_runs]
    assert kinds == ["agent", "end_event"]
    fake_llm.complete.assert_not_called()  # no target fields -> no data-adapter LLM call needed


@pytest.mark.asyncio
async def test_run_orchestration_agent_error_stops_the_run(fake_llm, monkeypatch):
    error_outcome = _agent_outcome(status="FAILED", graded_passed=None, deviations=[TwinDeviation(reason="bad JSON", step_index=None)])
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=error_outcome))
    flow_nodes = _linear_flow_nodes()
    node_artifacts = {"task": NodeArtifact(artifact_id="art_1", definition=_definition(), tool_schemas={})}

    outcome = await run_orchestration(
        fake_llm, flow_nodes=flow_nodes, node_artifacts=node_artifacts, scenario=_scenario(), max_turns_per_node=8
    )

    assert outcome.status == "FAILED"
    assert outcome.node_runs[0].status == "FAILED"
    assert any("bad JSON" in d.reason for d in outcome.deviations)


# -- run_orchestration: gateways --------------------------------------------------


def _gateway_flow_nodes() -> list[FlowNodeInfo]:
    return [
        FlowNodeInfo(
            id="start", label="Start", bpmn_type="startEvent", lane_name=None,
            predecessors=[], successors=[FlowRef(node_id="gw", label="Gateway", condition=None)],
        ),
        FlowNodeInfo(
            id="gw", label="Gateway", bpmn_type="exclusiveGateway", lane_name=None,
            predecessors=[FlowRef(node_id="start", label="Start", condition=None)],
            successors=[
                FlowRef(node_id="end_a", label="End A", condition="approved"),
                FlowRef(node_id="end_b", label="End B", condition="rejected"),
            ],
        ),
        FlowNodeInfo(
            id="end_a", label="End A", bpmn_type="endEvent", lane_name=None,
            predecessors=[FlowRef(node_id="gw", label="Gateway", condition=None)], successors=[],
        ),
        FlowNodeInfo(
            id="end_b", label="End B", bpmn_type="endEvent", lane_name=None,
            predecessors=[FlowRef(node_id="gw", label="Gateway", condition=None)], successors=[],
        ),
    ]


@pytest.mark.asyncio
async def test_run_orchestration_gateway_without_decision_errors(fake_llm):
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow_nodes(), node_artifacts={}, scenario=_scenario(), max_turns_per_node=8
    )
    assert outcome.status == "FAILED"
    assert any("gw" in d.reason for d in outcome.deviations)


@pytest.mark.asyncio
async def test_run_orchestration_gateway_follows_configured_decision(fake_llm):
    scenario = _scenario(gateway_decisions={"gw": GatewayDecision(to_node_id="end_b")})
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow_nodes(), node_artifacts={}, scenario=scenario, max_turns_per_node=8
    )
    assert outcome.status == "COMPLETED"
    assert outcome.visited_path == ["start", "gw", "end_b"]


@pytest.mark.asyncio
async def test_run_orchestration_gateway_rejects_invalid_target(fake_llm):
    scenario = _scenario(gateway_decisions={"gw": GatewayDecision(to_node_id="nonexistent")})
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_gateway_flow_nodes(), node_artifacts={}, scenario=scenario, max_turns_per_node=8
    )
    assert outcome.status == "FAILED"


# -- run_orchestration: diagrams with no explicit start/end events ---------------
# Finalize only checks XML well-formedness (validate_bpmn_integrity), not
# narrative completeness, so a real finalized diagram can legitimately be
# plain tasks/gateways with no startEvent/endEvent at all -- found by
# testing against a real finalized process (see decision-log.md).


def _no_events_flow_nodes() -> list[FlowNodeInfo]:
    return [
        FlowNodeInfo(
            id="task1", label="Task 1", bpmn_type="userTask", lane_name=None,
            predecessors=[], successors=[FlowRef(node_id="task2", label="Task 2", condition=None)],
        ),
        FlowNodeInfo(
            id="task2", label="Task 2", bpmn_type="userTask", lane_name=None,
            predecessors=[FlowRef(node_id="task1", label="Task 1", condition=None)], successors=[],
        ),
    ]


@pytest.mark.asyncio
async def test_run_orchestration_falls_back_to_root_node_with_no_start_event(fake_llm):
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_no_events_flow_nodes(), node_artifacts={}, scenario=_scenario(), max_turns_per_node=8
    )
    assert outcome.status == "COMPLETED"
    assert outcome.visited_path == ["task1", "task2"]
    assert [nr.kind for nr in outcome.node_runs] == ["manual", "manual"]
    assert outcome.final_output == outcome.node_runs[-1].output


@pytest.mark.asyncio
async def test_run_orchestration_raises_on_ambiguous_entry_points(fake_llm):
    """Epic 17: a background task must never let an exception escape (see
    app/twin/engine.py's docstring, decision 4 in the epic's plan) -- this
    now lands as status="FAILED" with a deviation, not a raised exception,
    the same way any other TwinServiceError (a data-adaptation failure, an
    unknown tool call) does."""
    flow_nodes = [
        FlowNodeInfo(id="task1", label="Task 1", bpmn_type="userTask", lane_name=None, predecessors=[], successors=[]),
        FlowNodeInfo(id="task2", label="Task 2", bpmn_type="userTask", lane_name=None, predecessors=[], successors=[]),
    ]
    outcome = await run_orchestration(
        fake_llm, flow_nodes=flow_nodes, node_artifacts={}, scenario=_scenario(), max_turns_per_node=8
    )
    assert outcome.status == "FAILED"
    assert any("ambiguous" in d.reason for d in outcome.deviations)


# -- run_orchestration: manual (non-automatable) nodes ----------------------------


@pytest.mark.asyncio
async def test_run_orchestration_manual_node_defaults_to_human_checkpoint(fake_llm):
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts={}, scenario=_scenario(), max_turns_per_node=8
    )
    assert outcome.status == "COMPLETED"
    manual_run = outcome.node_runs[0]
    assert manual_run.kind == "manual"
    assert manual_run.output["human_decision"] == "approve"  # default approve_probability=1.0
    assert any(s.type == "HUMAN_APPROVED" and s.decision == "approve" for s in manual_run.steps)


@pytest.mark.asyncio
async def test_run_orchestration_manual_node_fixed_stub(fake_llm):
    scenario = _scenario(
        manual_node_config={"task": ManualNodeConfig(mode="fixed_stub", fixed_output={"filed": True})}
    )
    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts={}, scenario=scenario, max_turns_per_node=8
    )
    assert outcome.status == "COMPLETED"
    assert outcome.node_runs[0].output == {"filed": True}
    assert outcome.final_output == {"filed": True}


# -- run_orchestration: data handoff (folded into AGENT_DELEGATED steps) ---------


@pytest.mark.asyncio
async def test_run_orchestration_llm_adapter_reshapes_input(fake_llm, monkeypatch):
    captured_scenarios = []

    async def fake_run_scenario(llm, *, artifact, tool_schemas, scenario, max_turns, **kwargs):
        captured_scenarios.append(scenario)
        return _agent_outcome()

    monkeypatch.setattr(orchestrator_module, "run_scenario", fake_run_scenario)
    fake_llm.complete.return_value = _llm_result(text='{"foo": "bar"}')

    definition = _definition(input_schema=[AgentIOField(name="foo", source_or_destination="upstream", format="string")])
    node_artifacts = {"task": NodeArtifact(artifact_id="art_1", definition=definition, tool_schemas={})}

    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts=node_artifacts, scenario=_scenario(inputs={"raw": "data"}), max_turns_per_node=8
    )

    assert outcome.status == "COMPLETED"
    assert captured_scenarios[0].inputs == {"foo": "bar"}
    delegated = next(s for s in outcome.steps if s.type == "AGENT_DELEGATED")
    assert delegated.arguments["mode"] == "llm_adapter"
    assert delegated.result["input_after"] == {"foo": "bar"}


@pytest.mark.asyncio
async def test_run_orchestration_exact_field_contract_missing_field_errors(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=_agent_outcome()))
    definition = _definition(input_schema=[AgentIOField(name="missing_field", source_or_destination="upstream", format="string")])
    node_artifacts = {"task": NodeArtifact(artifact_id="art_1", definition=definition, tool_schemas={})}
    scenario = _scenario(inputs={"other": 1}, data_mapping_mode={"task": "exact_field_contract"})

    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts=node_artifacts, scenario=scenario, max_turns_per_node=8
    )

    assert outcome.status == "FAILED"
    assert outcome.node_runs[0].status == "FAILED"
    assert "missing_field" in outcome.node_runs[0].error_message
    fake_llm.complete.assert_not_called()  # exact contract mode never calls the LLM


# -- run_orchestration: step guard -------------------------------------------------


@pytest.mark.asyncio
async def test_run_orchestration_stops_on_max_total_steps(fake_llm):
    # A gateway that always routes back to itself -- an unconditional loop.
    flow_nodes = [
        FlowNodeInfo(
            id="start", label="Start", bpmn_type="startEvent", lane_name=None,
            predecessors=[], successors=[FlowRef(node_id="gw", label="Gateway", condition=None)],
        ),
        FlowNodeInfo(
            id="gw", label="Gateway", bpmn_type="exclusiveGateway", lane_name=None,
            predecessors=[], successors=[FlowRef(node_id="gw", label="Gateway", condition=None)],
        ),
    ]
    scenario = _scenario(gateway_decisions={"gw": GatewayDecision(to_node_id="gw")})
    outcome = await run_orchestration(
        fake_llm, flow_nodes=flow_nodes, node_artifacts={}, scenario=scenario, max_turns_per_node=8, max_total_steps=5
    )
    assert outcome.status == "FAILED"
    assert any("Exceeded max total steps" in d.reason for d in outcome.deviations)


@pytest.mark.asyncio
async def test_run_orchestration_calls_on_node_run_callback_incrementally(fake_llm, monkeypatch):
    monkeypatch.setattr(orchestrator_module, "run_scenario", AsyncMock(return_value=_agent_outcome()))
    node_artifacts = {"task": NodeArtifact(artifact_id="art_1", definition=_definition(), tool_schemas={})}
    seen = []

    async def on_node_run(node_run):
        seen.append(node_run.node_id)

    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts=node_artifacts, scenario=_scenario(),
        max_turns_per_node=8, on_node_run=on_node_run,
    )

    assert seen == ["task", "end"]
    assert seen == [nr.node_id for nr in outcome.node_runs]


# -- Epic 17: manual human-in-the-loop pause/resume at a manual node -------------


@pytest.mark.asyncio
async def test_run_orchestration_manual_node_manual_checkpoint_suspends_and_resumes(fake_llm):
    from app.schemas.twin import TwinHumanCheckpointConfig

    scenario = _scenario(
        manual_node_config={"task": ManualNodeConfig(human_checkpoint_config=TwinHumanCheckpointConfig(mode="manual"))}
    )

    suspended = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts={}, scenario=scenario, max_turns_per_node=8
    )

    assert suspended.status == "WAITING_FOR_HUMAN"
    assert suspended.resume_state is not None
    assert suspended.resume_state["manual"] is True

    resumed = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts={}, scenario=scenario, max_turns_per_node=8,
        resume_state=suspended.resume_state, human_decision="reject",
    )

    assert resumed.status == "COMPLETED"
    assert resumed.node_runs[0].output["human_decision"] == "reject"


# -- Epic 17: cooperative cancellation --------------------------------------------


@pytest.mark.asyncio
async def test_run_orchestration_cancelled_via_on_step(fake_llm):
    async def on_step(step):
        if step.type == "RUN_STARTED":
            raise RunCancelled()

    outcome = await run_orchestration(
        fake_llm, flow_nodes=_linear_flow_nodes(), node_artifacts={}, scenario=_scenario(),
        max_turns_per_node=8, on_step=on_step,
    )

    assert outcome.status == "CANCELLED"
