"""Epic 14 (core slice), US14.2/US14.3: the digital twin execution loop.

Epic 39, US39.1: the loop itself now lives in app/runtime/executor.py,
shared with production. This module supplies the twin's side of it -- the
tool port (faults, stubs, sample data, sandbox API, LLM-fabricated
responses), the simulated reviewer, the run instructions -- and grades the
result. What follows describes the twin run as a whole, wherever each
piece now sits.

Runs a generated agent artifact (Epic 12) for real against `LLMClient`, with
every `tools_systems_needed` entry and (if the artifact has one) its
human_checkpoint wired to a simulated counterpart (app/twin/simulators.py)
instead of a live system or a live person -- see planning/epics/
14-digital-twin-simulation.md's "Discovery" section for the model this
implements.

Human checkpoints are deliberately modeled as one more callable tool
(`request_human_decision`), not special-cased control flow -- the same
tool-calling loop mechanically handles "call a system" and "ask a human"
via one code path. The stored `AgentDefinition.system_prompt` itself is
never modified; the twin-only instructions (which tool maps to which
system, how to ask for human review, how to report the final answer) are
appended as a separate system message that only exists for this run.

Epic 20: every tool call is checked against the artifact's own declared
governance (permissions, tool contracts) via app/twin/gateway.py's
check_tool_permission before app/twin/simulators.py ever produces a
response, and the whole run is bounded by the artifact's RuntimeGuardrails
(steps, tool calls, wall-clock time, model calls, tokens, cost, loop
detection) via gateway.py's GuardrailTracker.

Epic 21: when the scenario references a TwinEnvironment, every tool call is
first offered to that environment's fault catalog (app/twin/
environments.py's `roll_fault`) -- a triggered fault short-circuits the
normal Proxy/Static resolution below with a simulated timeout/error/
malformed response instead.

Epic 17: replaces the old three-way `passed|failed|error` TwinRunStatus and
two-kind TwinTraceStep with the unified RunStatus/RunStep taxonomy
(app/schemas/run.py) shared with app/twin/orchestrator.py, and adds three
things Epic 14 explicitly deferred: real background execution (the caller
now drives this via `on_step`/`on_phase` rather than awaiting one big
coroutine to completion inside a request -- see app/db/repository.py),
cooperative cancellation (`RunCancelled`, raised by the caller's `on_step`),
and genuine suspend/resume for a "manual" human checkpoint (`resume_state`/
`human_decision` below) instead of only the auto-resolved probability/rule
modes. Status now describes execution outcome only -- grading (did the
trace/output match a scenario's expectations) is `graded_passed`/
`deviations`, computed independently (see `grade_run`)."""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.llm import LLMClient, ToolCall, ToolDefinition
from app.runtime.executor import ExecutionOutcome, RunCancelled, ToolResult, execute
from app.runtime.executor import parse_json_object as _parse_json_object
from app.runtime.instructions import ROUTE_FIELD, run_instructions
from app.schemas.agents import AgentDefinition
from app.schemas.environment import SimulatedSystemData, TwinEnvironment
from app.schemas.run import RunStatus, RunStep, TwinDeviation
from app.schemas.twin import (
    InferredToolSchema,
    TwinExpectedStep,
    TwinHumanCheckpointConfig,
    TwinScenario,
    TwinSystemStub,
)

from .environments import roll_fault, roll_faults
from .records import RecordsSession, find_system_data, records_tool_schema
from .compliance import grade_compliance
from .simulators import resolve_human_decision, resolve_tool_call

# RunCancelled, _parse_json_object and ROUTE_FIELD moved to app/runtime and
# stay importable from here for existing callers.
__all__ = ["ROUTE_FIELD", "RunCancelled", "RunOutcome", "_parse_json_object", "grade_run", "run_scenario"]

_MISSING = object()


@dataclass
class RunOutcome:
    status: RunStatus
    steps: list[RunStep] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = field(default_factory=list)
    graded_passed: bool | None = None
    total_cost_usd: float | None = None
    total_tokens: int = 0
    turns_used: int = 0
    # Epic 17, US17.3: only set when status == "WAITING_FOR_HUMAN" -- opaque,
    # JSON-serializable continuation state a caller persists and later
    # passes back as `resume_state` to continue this exact run.
    resume_state: dict[str, Any] | None = None


def _grade_steps_in_order(
    expected: list[TwinExpectedStep], actionable: list[RunStep], actual_kind: Callable[[RunStep], str]
) -> list[TwinDeviation]:
    """Epic 24, US24.4: `step_match="in_order"` -- each expected step must
    be matched by a later actual step than the previous match; extra
    actual steps in between (retries, extra lookups) are fine."""
    deviations: list[TwinDeviation] = []
    cursor = 0
    for exp in expected:
        match_index = None
        for j in range(cursor, len(actionable)):
            act = actionable[j]
            if actual_kind(act) != exp.kind:
                continue
            if exp.kind == "tool_call" and act.target != exp.target:
                continue
            if exp.kind == "human_checkpoint" and exp.expected_decision is not None and act.decision != exp.expected_decision:
                continue
            match_index = j
            break
        if match_index is None:
            if exp.kind == "tool_call":
                reason = f"Expected the agent to use '{exp.target}', but it didn't (at that point in the run)"
            elif exp.expected_decision is not None:
                reason = f"Expected a reviewer to be asked and to {exp.expected_decision}, but that didn't happen"
            else:
                reason = "Expected a reviewer to be asked, but the agent never asked"
            deviations.append(TwinDeviation(reason=reason))
        else:
            cursor = match_index + 1
    return deviations


def grade_run(
    steps: list[RunStep], final_output: dict[str, Any] | None, scenario: TwinScenario
) -> tuple[bool, list[TwinDeviation]]:
    """US14.3: pass/fail is the trace (which simulated systems/checkpoints
    were hit, in what order, with what checkpoint decisions) compared
    pairwise against `expected_steps`, plus the final output compared
    key-by-key against `expected_outputs` -- not an LLM-judge over free
    text (decided in the design discussion Epic 14's planning doc records).

    Epic 17: filters `steps` down to the subsequence that represents an
    actual resolved action (a tool call or a human checkpoint resolving) --
    the same two kinds Epic 14's original flat trace ever contained -- and
    grades that subsequence exactly as before. Returns `graded_passed`
    rather than a run status: grading is decoupled from execution lifecycle
    (see this module's docstring)."""
    deviations: list[TwinDeviation] = []
    expected = scenario.expected_steps
    actionable = [s for s in steps if s.type in ("TOOL_EXECUTED", "TOOL_FAILED", "HUMAN_APPROVED", "HUMAN_REJECTED")]

    def actual_kind(step: RunStep) -> str:
        return "tool_call" if step.type in ("TOOL_EXECUTED", "TOOL_FAILED") else "human_checkpoint"

    if scenario.step_match == "in_order":
        deviations.extend(_grade_steps_in_order(expected, actionable, actual_kind))
        expected = []
        actionable = []

    for i in range(max(len(expected), len(actionable))):
        exp = expected[i] if i < len(expected) else None
        act = actionable[i] if i < len(actionable) else None
        if exp is None and act is not None:
            deviations.append(
                TwinDeviation(reason=f"Unexpected extra step at index {i}: {actual_kind(act)} '{act.target}'", step_index=i)
            )
        elif act is None and exp is not None:
            deviations.append(
                TwinDeviation(
                    reason=f"Missing expected step at index {i}: {exp.kind} '{exp.target or ''}'", step_index=i
                )
            )
        elif exp is not None and act is not None:
            kind = actual_kind(act)
            if kind != exp.kind or (exp.kind == "tool_call" and act.target != exp.target):
                deviations.append(
                    TwinDeviation(reason=f"Step {i}: expected {exp.kind} '{exp.target}', got {kind} '{act.target}'", step_index=i)
                )
            elif exp.kind == "human_checkpoint" and exp.expected_decision is not None and act.decision != exp.expected_decision:
                deviations.append(
                    TwinDeviation(
                        reason=(
                            f"Step {i}: expected human checkpoint decision '{exp.expected_decision}', "
                            f"got '{act.decision}'"
                        ),
                        step_index=i,
                    )
                )

    for key, expected_value in scenario.expected_outputs.items():
        actual_value = (final_output or {}).get(key, _MISSING)
        if actual_value is _MISSING:
            deviations.append(TwinDeviation(reason=f"Expected output field '{key}' was missing from the final output"))
        elif expected_value is not None and actual_value != expected_value:
            deviations.append(
                TwinDeviation(reason=f"Expected output field '{key}' was '{actual_value}', expected '{expected_value}'")
            )

    # Epic 30, US30.5: control behaviour the situation asserts.
    deviations.extend(grade_compliance(compliance_assertions(scenario), steps, final_output, "COMPLETED"))
    # Epic 32, US32.2: a dataset row's expected outcome.
    deviations.extend(grade_decision(scenario, final_output, "COMPLETED"))

    return not deviations, deviations


def grade_decision(scenario: TwinScenario, final_output: dict[str, Any] | None, status: str) -> list[TwinDeviation]:
    if scenario.expected_decision is None:
        return []
    from app.datasets.runtime import decision_from_output

    actual = "escalate" if status == "ESCALATED" else decision_from_output(final_output, scenario.decision_field)
    if actual == scenario.expected_decision:
        return []
    shown = actual or f"no '{scenario.decision_field}' in the output"
    return [TwinDeviation(reason=f"Expected the case to be decided '{scenario.expected_decision}', but it was '{shown}'")]


def compliance_assertions(scenario: TwinScenario) -> list:
    """Epic 30: assertions live on the situation's story (they're authored
    there and need no separate compiled field)."""
    return list(scenario.story.expect.compliance) if scenario.story is not None else []


@dataclass
class _TwinTools:
    """Epic 39's ToolPort for the twin: a call the gateway allowed is
    answered by a simulated fault (Epic 21, US24.6), else sample data
    (Epic 36), else a static stub, a sandbox API or an LLM-fabricated
    response (Epic 14, Epic 29). The order is unchanged from before the
    loop moved to app/runtime/executor.py."""

    llm: LLMClient
    scenario: TwinScenario
    environment: TwinEnvironment | None
    tool_schemas: dict[str, InferredToolSchema]
    records_data: dict[str, SimulatedSystemData]
    rng: random.Random
    records_session: RecordsSession = field(default_factory=RecordsSession)

    async def call(self, system_name: str, call: ToolCall) -> ToolResult:
        # Epic 21, US21.2: a fault intercepts the call before Epic 14's
        # Proxy/Static resolution ever runs. Epic 24, US24.6: the
        # scenario's own faults are offered first, then the environment's.
        fault = roll_faults(self.scenario.faults.get(system_name, []), self.rng)
        if fault is None and self.environment is not None:
            fault = roll_fault(self.environment, system_name, self.rng)
        if fault is not None:
            return ToolResult(response=fault.result, failed=fault.is_error, reason=f"Simulated fault: {fault.type}")
        stub = self.scenario.system_stubs.get(system_name) or TwinSystemStub()
        if self.scenario.stub_latency_ms:
            await asyncio.sleep(self.scenario.stub_latency_ms / 1000)
        if system_name in self.records_data:
            response = self.records_session.call(self.records_data[system_name], system_name, call.arguments)
            return ToolResult(response=response)
        response, static_fallback, llm_call = await resolve_tool_call(
            self.llm, system_name=system_name, call_arguments=call.arguments,
            schema=self.tool_schemas[system_name], stub=stub,
        )
        return ToolResult(response=response, static_fallback=static_fallback, llm_usage=llm_call)


@dataclass
class _TwinReviewer:
    """Epic 39's HumanPort for the twin: a sampled or rule-based reviewer,
    or none at all in manual mode, which suspends the run until someone
    resumes it (Epic 17)."""

    config: TwinHumanCheckpointConfig
    rng: random.Random

    async def decide(self, proposed_action: dict[str, Any]) -> Literal["approve", "reject"] | None:
        if self.config.mode == "manual":
            return None
        return resolve_human_decision(proposed_action, self.config, self.rng)


def _sample_data_systems(
    tool_schemas: dict[str, InferredToolSchema], scenario: TwinScenario, environment: TwinEnvironment | None
) -> dict[str, SimulatedSystemData]:
    """Epic 36, US36.1: systems the environment has sample data for get a
    tool generated from that data, answered from it. Static and API stubs
    still win over sample data."""
    return {
        name: data for name in tool_schemas
        if (scenario.system_stubs.get(name) or TwinSystemStub()).mode == "proxy"
        and (data := find_system_data(environment, name)) is not None
    }


def _output_fields(
    artifact: AgentDefinition, scenario: TwinScenario, routing: tuple[str, list[str]] | None
) -> tuple[list[str], str | None]:
    """The keys the agent must answer with, and the decision field if the
    run is graded on one."""
    output_fields = [f.name for f in artifact.output_schema]
    # Epic 32: a data-driven run is graded on the case decision, so the
    # agent must report one even when its own output schema has no such
    # field.
    decision_field = scenario.decision_field if scenario.expected_decision is not None else None
    if decision_field and output_fields and decision_field not in output_fields:
        output_fields.append(decision_field)
    if routing and output_fields and ROUTE_FIELD not in output_fields:
        output_fields.append(ROUTE_FIELD)
    return output_fields, decision_field


def _graded(outcome: ExecutionOutcome, scenario: TwinScenario) -> RunOutcome:
    """Grades a finished execution against the scenario. Status says how
    the run ended; graded_passed/deviations say whether that matched what
    the scenario expected."""
    steps, final_output, status = outcome.steps, outcome.final_output, outcome.status
    graded_passed: bool | None = None
    deviations: list[TwinDeviation] = []
    if status == "COMPLETED":
        graded_passed, deviations = grade_run(steps, final_output, scenario)
        # A policy-denied tool call is always at least a reportable
        # deviation, even if the agent recovered and still produced a
        # grading-correct final output.
        graded_passed = graded_passed and not outcome.policy_deviations
    elif status == "ESCALATED" and (compliance_assertions(scenario) or scenario.expected_decision):
        # Epic 30, US30.5: for a rule that demands escalation, an agent
        # that escalated did the right thing -- grade its assertions rather
        # than leaving the run ungraded. Epic 32: likewise a dataset row
        # whose expected outcome is "escalate".
        deviations = grade_compliance(compliance_assertions(scenario), steps, final_output, status)
        deviations += grade_decision(scenario, final_output, status)
        graded_passed = not deviations
    deviations = outcome.policy_deviations + deviations
    if outcome.terminal_deviation is not None:
        deviations.append(outcome.terminal_deviation)
    return RunOutcome(
        status=status,
        steps=steps,
        final_output=final_output,
        deviations=deviations,
        graded_passed=graded_passed,
        total_cost_usd=outcome.total_cost_usd,
        total_tokens=outcome.total_tokens,
        turns_used=outcome.turns_used,
    )


async def run_scenario(
    llm: LLMClient,
    *,
    artifact: AgentDefinition,
    tool_schemas: dict[str, InferredToolSchema],
    scenario: TwinScenario,
    max_turns: int,
    environment: TwinEnvironment | None = None,
    resume_state: dict[str, Any] | None = None,
    human_decision: Literal["approve", "reject"] | None = None,
    node_id: str | None = None,
    on_step: Callable[[RunStep], Awaitable[None]] | None = None,
    on_phase: Callable[[RunStatus], Awaitable[None]] | None = None,
    routing: tuple[str, list[str]] | None = None,
) -> RunOutcome:
    """Runs one scenario through the shared executor (app/runtime/
    executor.py) with the twin's tools and reviewer, then grades it."""
    records_data = _sample_data_systems(tool_schemas, scenario, environment)
    tool_schemas = {
        name: (records_tool_schema(name, records_data[name]) if name in records_data else schema)
        for name, schema in tool_schemas.items()
    }
    tools = {
        name: ToolDefinition(name=schema.tool_name, description=schema.description, parameters=schema.parameters)
        for name, schema in tool_schemas.items()
    }
    output_fields, decision_field = _output_fields(artifact, scenario, routing)
    # One generator for faults and the reviewer, drawn in call order, so a
    # seeded run replays exactly (Epic 34, US34.6).
    rng = random.Random(scenario.human_checkpoint_config.seed)

    outcome = await execute(
        llm,
        artifact=artifact,
        tools=tools,
        tool_port=_TwinTools(
            llm=llm, scenario=scenario, environment=environment, tool_schemas=tool_schemas,
            records_data=records_data, rng=rng,
        ),
        human_port=_TwinReviewer(config=scenario.human_checkpoint_config, rng=rng),
        inputs=scenario.inputs,
        instructions=run_instructions(
            has_checkpoint=artifact.human_checkpoint != "none", output_fields=output_fields,
            decision_field=decision_field, routing=routing, test_run=True,
        ),
        output_fields=output_fields,
        max_turns=max_turns,
        operation="twin_run",
        resume_state=resume_state,
        human_decision=human_decision,
        node_id=node_id,
        on_step=on_step,
        on_phase=on_phase,
    )
    if outcome.status == "WAITING_FOR_HUMAN":
        return RunOutcome(status=outcome.status, steps=outcome.steps, resume_state=outcome.resume_state)
    return _graded(outcome, scenario)
