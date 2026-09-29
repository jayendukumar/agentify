import random
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from app.llm.types import ChatCompletionResult, ToolCall, Usage
from app.schemas.agents import AgentDefinition, EscalationPolicy, ResourcePermission, RuntimeGuardrails, ToolContract
from app.schemas.run import RunStep
from app.schemas.twin import (
    HumanDecisionRule,
    InferredToolSchema,
    StaticResponseRule,
    TwinExpectedStep,
    TwinHumanCheckpointConfig,
    TwinScenario,
    TwinSystemStub,
)
from app.twin.engine import grade_run, run_scenario
from app.twin.errors import TwinServiceError
from app.twin.simulators import resolve_human_decision, resolve_tool_call


def _crm_schema() -> InferredToolSchema:
    return InferredToolSchema(
        system_name="CRM system",
        tool_name="lookup_customer",
        description="Look up a customer record by id.",
        parameters={"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
        response_shape_description="An object with a 'tier' field.",
    )


def _scenario(**overrides) -> TwinScenario:
    defaults = dict(
        id="twinsc_1",
        agent_artifact_id="agent_1",
        name="Happy path",
        inputs={"customer_id": "123"},
        system_stubs={},
        human_checkpoint_config=TwinHumanCheckpointConfig(),
        expected_steps=[],
        expected_outputs={},
        created_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return TwinScenario(**defaults)


def _llm_result(*, tool_calls=None, text=None) -> ChatCompletionResult:
    return ChatCompletionResult(
        text=text,
        tool_calls=tool_calls or [],
        finish_reason="tool_calls" if tool_calls else "stop",
        usage=Usage(input_tokens=1, output_tokens=1, total_tokens=2),
        model="test-model",
    )


def _step(type: str, **kwargs) -> RunStep:
    kwargs.setdefault("seq", 0)
    kwargs.setdefault("occurred_at", datetime.now(timezone.utc))
    return RunStep(type=type, **kwargs)


def _actionable_types(steps: list[RunStep]) -> list[str]:
    return [s.type for s in steps if s.type in ("TOOL_EXECUTED", "TOOL_FAILED", "HUMAN_APPROVED", "HUMAN_REJECTED")]


# -- resolve_human_decision ----------------------------------------------------


def test_resolve_human_decision_probability_mode_approve():
    config = TwinHumanCheckpointConfig(mode="probability", approve_probability=1.0)
    assert resolve_human_decision({}, config, random.Random(1)) == "approve"


def test_resolve_human_decision_probability_mode_reject():
    config = TwinHumanCheckpointConfig(mode="probability", approve_probability=0.0)
    assert resolve_human_decision({}, config, random.Random(1)) == "reject"


def test_resolve_human_decision_rule_mode():
    config = TwinHumanCheckpointConfig(
        mode="rule", rule=HumanDecisionRule(field="amount", operator="gt", value=5000, on_true="reject", on_false="approve")
    )
    assert resolve_human_decision({"amount": 6000}, config, random.Random()) == "reject"
    assert resolve_human_decision({"amount": 100}, config, random.Random()) == "approve"


def test_resolve_human_decision_rule_missing_field_raises():
    config = TwinHumanCheckpointConfig(
        mode="rule", rule=HumanDecisionRule(field="amount", operator="gt", value=5000, on_true="reject", on_false="approve")
    )
    with pytest.raises(TwinServiceError):
        resolve_human_decision({}, config, random.Random())


# -- resolve_tool_call ----------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_tool_call_static_match_never_calls_llm():
    llm = AsyncMock()
    stub = TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"tier": "gold"})])

    response, fallback, llm_call = await resolve_tool_call(
        llm, system_name="CRM system", call_arguments={"id": "1"}, schema=_crm_schema(), stub=stub
    )

    assert response == {"tier": "gold"}
    assert fallback is False
    assert llm_call is None
    llm.complete.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_tool_call_static_no_match_falls_back_to_proxy():
    llm = AsyncMock()
    llm.complete.return_value = _llm_result(text='{"tier": "platinum"}')
    stub = TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={"id": "999"}, response={"tier": "none"})])

    response, fallback, llm_call = await resolve_tool_call(
        llm, system_name="CRM system", call_arguments={"id": "1"}, schema=_crm_schema(), stub=stub
    )

    assert response == {"tier": "platinum"}
    assert fallback is True
    assert llm_call is not None
    llm.complete.assert_awaited_once()
    assert llm.complete.call_args.kwargs["operation"] == "twin_system_simulation"


@pytest.mark.asyncio
async def test_resolve_tool_call_proxy_mode_calls_llm():
    llm = AsyncMock()
    llm.complete.return_value = _llm_result(text='{"tier": "silver"}')

    response, fallback, llm_call = await resolve_tool_call(
        llm, system_name="CRM system", call_arguments={"id": "1"}, schema=_crm_schema(), stub=TwinSystemStub(mode="proxy")
    )

    assert response == {"tier": "silver"}
    assert fallback is False
    assert llm_call == (Usage(input_tokens=1, output_tokens=1, total_tokens=2), "test-model")


# -- grade_run --------------------------------------------------------------


def test_grade_run_passes_when_trace_and_output_match():
    steps = [
        _step("TOOL_EXECUTED", target="CRM system", result={"tier": "gold"}),
        _step("HUMAN_APPROVED", target="human_checkpoint", decision="approve"),
    ]
    scenario = _scenario(
        expected_steps=[
            TwinExpectedStep(kind="tool_call", target="CRM system"),
            TwinExpectedStep(kind="human_checkpoint", expected_decision="approve"),
        ],
        expected_outputs={"confirmation": None, "status": "sent"},
    )

    graded_passed, deviations = grade_run(steps, {"confirmation": "abc123", "status": "sent"}, scenario)

    assert graded_passed is True
    assert deviations == []


def test_grade_run_reports_first_mismatched_step():
    steps = [_step("TOOL_EXECUTED", target="Email system")]
    scenario = _scenario(expected_steps=[TwinExpectedStep(kind="tool_call", target="CRM system")])

    graded_passed, deviations = grade_run(steps, {}, scenario)

    assert graded_passed is False
    assert len(deviations) == 1
    assert deviations[0].step_index == 0
    assert "CRM system" in deviations[0].reason and "Email system" in deviations[0].reason


def test_grade_run_reports_missing_and_extra_steps():
    steps = [_step("TOOL_EXECUTED", target="CRM system"), _step("TOOL_EXECUTED", target="Email system")]
    scenario = _scenario(expected_steps=[TwinExpectedStep(kind="tool_call", target="CRM system")])

    graded_passed, deviations = grade_run(steps, {}, scenario)

    assert graded_passed is False
    assert any("extra step" in d.reason for d in deviations)


def test_grade_run_reports_missing_and_mismatched_output_fields():
    scenario = _scenario(expected_outputs={"confirmation": "XYZ", "status": "sent"})

    graded_passed, deviations = grade_run([], {"confirmation": "ABC"}, scenario)

    assert graded_passed is False
    reasons = " ".join(d.reason for d in deviations)
    assert "confirmation" in reasons
    assert "status" in reasons and "missing" in reasons.lower()


# -- run_scenario (full loop) -------------------------------------------------


@pytest.mark.asyncio
async def test_run_scenario_end_to_end_pass():
    artifact = AgentDefinition(
        name="Refund Agent",
        purpose="Process a refund request",
        trigger="Refund requested",
        system_prompt="You are Refund Agent.",
        input_schema=[],
        output_schema=[],
        tools_systems_needed=["CRM system"],
        human_checkpoint="review_before_action",
        model="test-model",
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(
        system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"tier": "gold"})])},
        human_checkpoint_config=TwinHumanCheckpointConfig(mode="probability", approve_probability=1.0),
        expected_steps=[
            TwinExpectedStep(kind="tool_call", target="CRM system"),
            TwinExpectedStep(kind="human_checkpoint", expected_decision="approve"),
        ],
        expected_outputs={"confirmation": None},
    )

    llm = AsyncMock()
    llm.complete.side_effect = [
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "123"})]),
        _llm_result(
            tool_calls=[
                ToolCall(
                    id="c2",
                    name="request_human_decision",
                    arguments={"summary": "Approve refund?", "proposed_action": {"tier": "gold"}},
                )
            ]
        ),
        _llm_result(text='{"confirmation": "sent"}'),
    ]

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert outcome.status == "COMPLETED"
    assert outcome.graded_passed is True
    assert outcome.deviations == []
    assert outcome.turns_used == 3
    assert outcome.final_output == {"confirmation": "sent"}
    assert _actionable_types(outcome.steps) == ["TOOL_EXECUTED", "HUMAN_APPROVED"]
    approved = next(s for s in outcome.steps if s.type == "HUMAN_APPROVED")
    assert approved.decision == "approve"
    assert outcome.total_tokens == 6
    # "test-model" has no entry in the pricing table (app/llm/usage.py) --
    # cost must come back None (unknown), never a silently wrong partial sum.
    assert outcome.total_cost_usd is None


@pytest.mark.asyncio
async def test_run_scenario_exceeds_max_turns_returns_failed():
    artifact = AgentDefinition(
        name="Loops Forever Agent",
        purpose="Never finishes",
        trigger="x",
        system_prompt="You are Loops Forever Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule()])})

    llm = AsyncMock()
    llm.complete.return_value = _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "1"})])

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=1)

    assert outcome.status == "FAILED"
    assert outcome.turns_used == 1
    assert any("without producing a final answer" in d.reason for d in outcome.deviations)


@pytest.mark.asyncio
async def test_run_scenario_exceeding_max_steps_escalates_when_policy_declared():
    """Epic 17, US17.5: the same "ran out of steps" condition becomes
    ESCALATED instead of FAILED once the artifact declares an escalation
    policy -- "the agent correctly asked for help" (well, hit its own
    declared limit) must be distinguishable from "the agent broke"."""
    artifact = AgentDefinition(
        name="Loops Forever Agent",
        purpose="Never finishes",
        trigger="x",
        system_prompt="You are Loops Forever Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
        escalation_policy=EscalationPolicy(escalate_when=["stuck in a loop"]),
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule()])})

    llm = AsyncMock()
    llm.complete.return_value = _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "1"})])

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=1)

    assert outcome.status == "ESCALATED"
    assert any(s.type == "ESCALATION" for s in outcome.steps)


# -- Epic 20: Tool Gateway / guardrail enforcement -----------------------------


@pytest.mark.asyncio
async def test_run_scenario_denies_call_to_undeclared_tool_and_feeds_back_denial():
    """An artifact that HAS declared governance (a non-empty tool_contracts
    list) denies a call to a system with no granted permission -- the
    denial is fed back to the model as a tool observation (like a real
    403), recorded as a TOOL_FAILED/denied=True step, and reported as a
    deviation even though the agent still produces a valid final output.
    Epic 17: a policy-denied call no longer downgrades the run's lifecycle
    *status* (still COMPLETED -- the run genuinely executed to a final
    response) -- only `graded_passed`, decoupled from status."""
    artifact = AgentDefinition(
        name="Refund Agent",
        purpose="Process a refund request",
        trigger="Refund requested",
        system_prompt="You are Refund Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
        tool_contracts=[ToolContract(system_name="CRM system", resource="customer", action="read")],
        permissions=[],  # nothing granted -- read on "customer" is NOT allowed
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(expected_steps=[TwinExpectedStep(kind="tool_call", target="CRM system")])

    llm = AsyncMock()
    llm.complete.side_effect = [
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "123"})]),
        _llm_result(text='{"confirmation": "handled"}'),
    ]

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert outcome.status == "COMPLETED"
    assert outcome.graded_passed is False
    denied_step = next(s for s in outcome.steps if s.type == "TOOL_FAILED")
    assert denied_step.denied is True
    assert denied_step.result["error"] == "permission_denied"
    assert any("denied by policy" in d.reason for d in outcome.deviations)
    # The agent still ran to completion using the denial as an observation,
    # not a crashed/aborted run.
    assert outcome.final_output == {"confirmation": "handled"}


@pytest.mark.asyncio
async def test_run_scenario_allows_call_when_permission_granted():
    artifact = AgentDefinition(
        name="Refund Agent",
        purpose="Process a refund request",
        trigger="Refund requested",
        system_prompt="You are Refund Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
        tool_contracts=[ToolContract(system_name="CRM system", resource="customer", action="read")],
        permissions=[ResourcePermission(resource="customer", actions={"read": True})],
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(
        system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule(match={}, response={"tier": "gold"})])},
        expected_steps=[TwinExpectedStep(kind="tool_call", target="CRM system")],
    )

    llm = AsyncMock()
    llm.complete.side_effect = [
        _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "123"})]),
        _llm_result(text='{"confirmation": "handled"}'),
    ]

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert outcome.status == "COMPLETED"
    assert outcome.graded_passed is True
    executed_step = next(s for s in outcome.steps if s.type == "TOOL_EXECUTED")
    assert executed_step.denied is False
    assert executed_step.result == {"tier": "gold"}


@pytest.mark.asyncio
async def test_run_scenario_stops_when_max_tool_calls_exceeded():
    artifact = AgentDefinition(
        name="Looping Agent",
        purpose="x",
        trigger="x",
        system_prompt="You are Looping Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
        guardrails=RuntimeGuardrails(max_tool_calls=1),
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule()])})

    llm = AsyncMock()
    llm.complete.return_value = _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "1"})])

    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert outcome.status == "FAILED"
    assert any("Exceeded max_tool_calls" in d.reason for d in outcome.deviations)


@pytest.mark.asyncio
async def test_run_scenario_effective_max_turns_clamped_by_artifact_guardrails():
    """An artifact's own RuntimeGuardrails.max_steps narrows the caller-
    supplied max_turns -- whichever is stricter wins."""
    artifact = AgentDefinition(
        name="Looping Agent",
        purpose="x",
        trigger="x",
        system_prompt="You are Looping Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
        guardrails=RuntimeGuardrails(max_steps=1),
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule()])})

    llm = AsyncMock()
    llm.complete.return_value = _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "1"})])

    # max_turns=5 (the global default) would allow 5 turns, but the
    # artifact's own guardrails.max_steps=1 is stricter and wins.
    outcome = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert outcome.status == "FAILED"
    assert outcome.turns_used == 1
    assert "Exceeded max_steps (1)" in outcome.deviations[-1].reason


# -- Epic 17: manual human-in-the-loop pause/resume (US17.3) -------------------


@pytest.mark.asyncio
async def test_run_scenario_manual_checkpoint_suspends_and_resumes():
    artifact = AgentDefinition(
        name="Refund Agent",
        purpose="Process a refund request",
        trigger="Refund requested",
        system_prompt="You are Refund Agent.",
        tools_systems_needed=["CRM system"],
        human_checkpoint="review_before_action",
        model="test-model",
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(human_checkpoint_config=TwinHumanCheckpointConfig(mode="manual"))

    llm = AsyncMock()
    llm.complete.side_effect = [
        _llm_result(
            tool_calls=[
                ToolCall(id="c1", name="request_human_decision", arguments={"summary": "ok?", "proposed_action": {"a": 1}})
            ]
        ),
        _llm_result(text='{"confirmation": "sent"}'),
    ]

    suspended = await run_scenario(llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5)

    assert suspended.status == "WAITING_FOR_HUMAN"
    assert suspended.resume_state is not None
    assert any(s.type == "HUMAN_APPROVAL_REQUESTED" for s in suspended.steps)

    resumed = await run_scenario(
        llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5,
        resume_state=suspended.resume_state, human_decision="approve",
    )

    assert resumed.status == "COMPLETED"
    assert resumed.final_output == {"confirmation": "sent"}
    approved = next(s for s in resumed.steps if s.type == "HUMAN_APPROVED")
    assert approved.decision == "approve"
    # Regression check (found via live testing against the real LLM): the
    # resumed call already had its request emitted before suspending --
    # resuming must not re-emit a second HUMAN_APPROVAL_REQUESTED for it.
    assert sum(1 for s in resumed.steps if s.type == "HUMAN_APPROVAL_REQUESTED") == 1


# -- Epic 17: cooperative cancellation (US17.4) --------------------------------


@pytest.mark.asyncio
async def test_run_scenario_cancelled_via_on_step():
    from app.twin.engine import RunCancelled

    artifact = AgentDefinition(
        name="Refund Agent",
        purpose="x",
        trigger="x",
        system_prompt="You are Refund Agent.",
        tools_systems_needed=["CRM system"],
        model="test-model",
    )
    tool_schemas = {"CRM system": _crm_schema()}
    scenario = _scenario(system_stubs={"CRM system": TwinSystemStub(mode="static", static_responses=[StaticResponseRule()])})

    llm = AsyncMock()
    llm.complete.return_value = _llm_result(tool_calls=[ToolCall(id="c1", name="lookup_customer", arguments={"id": "1"})])

    async def on_step(step):
        if step.type == "RUN_STARTED":
            raise RunCancelled()

    outcome = await run_scenario(
        llm, artifact=artifact, tool_schemas=tool_schemas, scenario=scenario, max_turns=5, on_step=on_step
    )

    assert outcome.status == "CANCELLED"
