"""Epic 14 (core slice), US14.2/US14.3: the digital twin execution loop.

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

import json
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from app.ids import utcnow
from app.llm import ChatMessage, LLMClient, ToolCall, ToolDefinition, Usage, estimate_cost_usd
from app.schemas.agents import AgentDefinition
from app.schemas.run import RunStatus, RunStep, StepType, TwinDeviation
from app.schemas.twin import InferredToolSchema, TwinScenario, TwinSystemStub

from .errors import TwinServiceError
from .gateway import GuardrailExceeded, GuardrailTracker, check_tool_permission
from .simulators import resolve_human_decision, resolve_tool_call

_HUMAN_DECISION_TOOL = ToolDefinition(
    name="request_human_decision",
    description=(
        "Call this to ask a human to review and approve or reject a proposed action, per your "
        "instructions above -- do not just describe that you would ask for review."
    ),
    parameters={
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "One sentence explaining what you want approved and why."},
            "proposed_action": {
                "type": "object",
                "description": "The key fields of the action/decision you want the human to review.",
            },
        },
        "required": ["summary", "proposed_action"],
    },
)

_MISSING = object()


class RunCancelled(Exception):
    """Epic 17, US17.4: raised by the caller's `on_step` callback the
    moment it observes the run's cancel_requested flag -- caught by the
    turn loop below and turned into status="CANCELLED", the same
    graceful-stop mechanism GuardrailExceeded already uses."""


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


@dataclass
class _Suspend:
    pending_calls: list[ToolCall]


def _twin_instructions(*, has_checkpoint: bool, output_fields: list[str]) -> str:
    lines = [
        "Digital twin simulation instructions for this test run only (not part of your normal "
        "operating instructions above):",
        "- Every system you have access to is available as a callable tool -- call the matching "
        "tool instead of describing what you would do.",
    ]
    if has_checkpoint:
        lines.append(
            '- When you need human review/approval as instructed above, call the '
            '"request_human_decision" tool instead of stopping -- you will get back a decision of '
            '"approve" or "reject" and should continue accordingly.'
        )
    if output_fields:
        lines.append(
            "- Once you are completely finished and have no more tool calls to make, respond with "
            f"ONLY a JSON object whose keys are exactly: {output_fields}. Do not include any other text."
        )
    else:
        lines.append(
            "- Once you are completely finished and have no more tool calls to make, respond with "
            "ONLY a short JSON object summarizing what you did. Do not include any other text."
        )
    return "\n".join(lines)


def _parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


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

    return not deviations, deviations


async def run_scenario(
    llm: LLMClient,
    *,
    artifact: AgentDefinition,
    tool_schemas: dict[str, InferredToolSchema],
    scenario: TwinScenario,
    max_turns: int,
    resume_state: dict[str, Any] | None = None,
    human_decision: Literal["approve", "reject"] | None = None,
    node_id: str | None = None,
    on_step: Callable[[RunStep], Awaitable[None]] | None = None,
    on_phase: Callable[[RunStatus], Awaitable[None]] | None = None,
) -> RunOutcome:
    tool_name_to_system = {schema.tool_name: system_name for system_name, schema in tool_schemas.items()}
    tools = [
        ToolDefinition(name=schema.tool_name, description=schema.description, parameters=schema.parameters)
        for schema in tool_schemas.values()
    ]
    has_checkpoint = artifact.human_checkpoint != "none"
    if has_checkpoint:
        tools.append(_HUMAN_DECISION_TOOL)

    output_fields = [f.name for f in artifact.output_schema]
    rng = random.Random(scenario.human_checkpoint_config.seed)

    steps: list[RunStep] = []
    total_tokens = 0
    total_cost = 0.0
    cost_incomplete = False
    policy_deviations: list[TwinDeviation] = []

    async def emit(step_type: StepType, **kwargs: Any) -> RunStep:
        step = RunStep(seq=len(steps), type=step_type, node_id=node_id, occurred_at=utcnow(), **kwargs)
        steps.append(step)
        if on_step is not None:
            await on_step(step)
        return step

    async def phase(status: RunStatus) -> None:
        if on_phase is not None:
            await on_phase(status)

    def account(model: str, usage: Usage) -> None:
        nonlocal total_tokens, total_cost, cost_incomplete
        total_tokens += usage.total_tokens
        cost = estimate_cost_usd(model, usage)
        if cost is None:
            cost_incomplete = True
        else:
            total_cost += cost

    # Epic 20: an artifact's own RuntimeGuardrails narrow (never widen) the
    # caller-supplied max_turns -- whichever is stricter wins.
    effective_max_turns = min(max_turns, artifact.guardrails.max_steps)

    pending_calls: list[ToolCall] = []
    if resume_state is None:
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=artifact.system_prompt),
            ChatMessage(role="system", content=_twin_instructions(has_checkpoint=has_checkpoint, output_fields=output_fields)),
            ChatMessage(role="user", content=json.dumps(scenario.inputs)),
        ]
        tracker = GuardrailTracker(guardrails=artifact.guardrails)
        next_turn = 1
    else:
        messages = [ChatMessage.model_validate(m) for m in resume_state["messages"]]
        tracker = GuardrailTracker.from_state(artifact.guardrails, resume_state["tracker"])
        steps.extend(RunStep.model_validate(s) for s in resume_state["steps"])
        pending_calls = [ToolCall.model_validate(c) for c in resume_state["pending_calls"]]
        next_turn = resume_state["turn"] + 1

    final_output: dict[str, Any] | None = None
    turns_used = resume_state["turn"] if resume_state is not None else 0
    terminal_status: RunStatus | None = None
    terminal_deviation: TwinDeviation | None = None

    async def process_calls(calls: list[ToolCall], *, decision_for_first: str | None) -> tuple[list[ChatMessage], _Suspend | None]:
        """Resolves `calls` in order, one tool-response ChatMessage per
        call. Stops early (returning the still-unresolved suffix, including
        the call it stopped on) the moment it reaches a manual-mode human
        checkpoint with no decision available yet."""
        tool_messages: list[ChatMessage] = []
        first_decision = decision_for_first
        for index, call in enumerate(calls):
            if call.name == _HUMAN_DECISION_TOOL.name:
                await phase("WAITING_FOR_TOOL")
                proposed_action = call.arguments.get("proposed_action")
                proposed_action = proposed_action if isinstance(proposed_action, dict) else {}
                # A resume's first pending call already had this emitted
                # before the suspend (that's exactly why it's the one we
                # suspended on) -- only emit it here when it's genuinely
                # being seen for the first time.
                resuming_this_call = index == 0 and decision_for_first is not None
                if not resuming_this_call:
                    await emit("HUMAN_APPROVAL_REQUESTED", target="human_checkpoint", arguments=call.arguments)
                if scenario.human_checkpoint_config.mode == "manual" and first_decision is None:
                    return tool_messages, _Suspend(pending_calls=calls[index:])
                if first_decision is not None:
                    decision = first_decision
                    first_decision = None
                else:
                    decision = resolve_human_decision(proposed_action, scenario.human_checkpoint_config, rng)
                await emit(
                    "HUMAN_APPROVED" if decision == "approve" else "HUMAN_REJECTED",
                    target="human_checkpoint",
                    arguments=call.arguments,
                    decision=decision,
                )
                tool_response: dict[str, Any] = {"decision": decision}
            else:
                await phase("WAITING_FOR_TOOL")
                system_name = tool_name_to_system.get(call.name)
                if system_name is None:
                    await emit("TOOL_FAILED", target=call.name, arguments=call.arguments, reason="unknown tool")
                    raise TwinServiceError(f"Agent called an unknown tool '{call.name}' that wasn't offered to it")
                tracker.before_tool_call(system_name, json.dumps(call.arguments, sort_keys=True, default=str))
                decision_check = check_tool_permission(artifact, system_name)
                await emit(
                    "POLICY_CHECK",
                    target=system_name,
                    arguments=call.arguments,
                    denied=not decision_check.allowed,
                    reason=decision_check.reason,
                )
                if not decision_check.allowed:
                    await emit(
                        "TOOL_FAILED", target=system_name, arguments=call.arguments,
                        result={"error": "permission_denied", "reason": decision_check.reason}, denied=True,
                    )
                    policy_deviations.append(
                        TwinDeviation(reason=f"Tool call to '{system_name}' denied by policy: {decision_check.reason}")
                    )
                    tool_response = {"error": "permission_denied", "reason": decision_check.reason}
                else:
                    schema = tool_schemas[system_name]
                    stub = scenario.system_stubs.get(system_name) or TwinSystemStub()
                    try:
                        tool_response, static_fallback, llm_call = await resolve_tool_call(
                            llm, system_name=system_name, call_arguments=call.arguments, schema=schema, stub=stub
                        )
                    except TwinServiceError as exc:
                        await emit("TOOL_FAILED", target=system_name, arguments=call.arguments, reason=str(exc))
                        raise
                    if llm_call is not None:
                        usage, model = llm_call
                        account(model, usage)
                        tracker.record_usage(usage.total_tokens, estimate_cost_usd(model, usage))
                    await emit(
                        "TOOL_EXECUTED", target=system_name, arguments=call.arguments, result=tool_response,
                        static_fallback=static_fallback,
                    )
            tool_messages.append(ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=json.dumps(tool_response)))
        return tool_messages, None

    try:
        if resume_state is None:
            await emit("RUN_STARTED")
        if pending_calls:
            tool_messages, suspend = await process_calls(pending_calls, decision_for_first=human_decision)
            messages.extend(tool_messages)
            if suspend is not None:
                return RunOutcome(
                    status="WAITING_FOR_HUMAN",
                    steps=steps,
                    resume_state={
                        "messages": [m.model_dump(mode="json") for m in messages],
                        "steps": [s.model_dump(mode="json") for s in steps],
                        "tracker": tracker.to_state(),
                        "pending_calls": [c.model_dump(mode="json") for c in suspend.pending_calls],
                        "turn": turns_used,
                    },
                )

        for turn in range(next_turn, effective_max_turns + 1):
            turns_used = turn
            await phase("WAITING_FOR_MODEL")
            tracker.before_model_call()
            result = await llm.complete(
                messages,
                operation="twin_run",
                tools=tools or None,
                model=artifact.model,
                response_format=None if tools else {"type": "json_object"},
            )
            account(result.model, result.usage)
            tracker.record_usage(result.usage.total_tokens, estimate_cost_usd(result.model, result.usage))
            await emit("MODEL_INVOKED", result={"has_tool_calls": bool(result.tool_calls)})

            if not result.tool_calls:
                final_output = _parse_json_object(result.text)
                break

            messages.append(ChatMessage(role="assistant", content=result.text or "", tool_calls=result.tool_calls))
            tool_messages, suspend = await process_calls(result.tool_calls, decision_for_first=None)
            messages.extend(tool_messages)
            if suspend is not None:
                return RunOutcome(
                    status="WAITING_FOR_HUMAN",
                    steps=steps,
                    resume_state={
                        "messages": [m.model_dump(mode="json") for m in messages],
                        "steps": [s.model_dump(mode="json") for s in steps],
                        "tracker": tracker.to_state(),
                        "pending_calls": [c.model_dump(mode="json") for c in suspend.pending_calls],
                        "turn": turn,
                    },
                )
        else:
            terminal_deviation = TwinDeviation(
                reason=f"Exceeded max_steps ({effective_max_turns}) without producing a final answer"
            )
            terminal_status = "ESCALATED" if artifact.escalation_policy.escalate_when else "FAILED"
            if terminal_status == "ESCALATED":
                await emit("ESCALATION", reason=terminal_deviation.reason)
    except RunCancelled:
        terminal_status = "CANCELLED"
        terminal_deviation = TwinDeviation(reason="Run was cancelled")
    except GuardrailExceeded as exc:
        terminal_deviation = TwinDeviation(reason=exc.reason)
        if exc.kind == "timeout":
            terminal_status = "TIMED_OUT"
        else:
            terminal_status = "ESCALATED" if artifact.escalation_policy.escalate_when else "FAILED"
            if terminal_status == "ESCALATED":
                await emit("ESCALATION", reason=exc.reason)
    except TwinServiceError as exc:
        terminal_status = "FAILED"
        terminal_deviation = TwinDeviation(reason=str(exc))
    except Exception as exc:  # last-resort net: a background task has nowhere else to send this
        terminal_status = "FAILED"
        terminal_deviation = TwinDeviation(reason=f"Unexpected error: {exc}")

    graded_passed: bool | None = None
    deviations: list[TwinDeviation]
    if terminal_status is None:
        if final_output is None:
            terminal_status = "FAILED"
            deviations = [TwinDeviation(reason="Final response was not a valid JSON object")]
        else:
            terminal_status = "COMPLETED"
            graded_passed, grading_deviations = grade_run(steps, final_output, scenario)
            deviations = grading_deviations
            if policy_deviations and graded_passed:
                # A policy-denied tool call is always at least a reportable
                # deviation, even if the agent recovered and still produced
                # a grading-correct final output.
                graded_passed = False
    else:
        deviations = []
    deviations = policy_deviations + deviations
    if terminal_deviation is not None:
        deviations.append(terminal_deviation)

    # These two bookend steps are best-effort telemetry, not a decision
    # point -- terminal_status is already final by this point, so a
    # cancellation racing in right at the end (on_step's RunCancelled) must
    # not retroactively override a run that genuinely finished on its own.
    try:
        if terminal_status == "COMPLETED":
            await emit("FINAL_RESPONSE", result=final_output)
        await emit("RUN_COMPLETED", result={"status": terminal_status})
    except RunCancelled:
        pass

    return RunOutcome(
        status=terminal_status,
        steps=steps,
        final_output=final_output,
        deviations=deviations,
        graded_passed=graded_passed,
        total_cost_usd=None if cost_incomplete else total_cost,
        total_tokens=total_tokens,
        turns_used=turns_used,
    )
