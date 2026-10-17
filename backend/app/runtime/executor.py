"""Epic 39, US39.1: the agent execution loop, shared by the twin and
production.

Moved out of app/twin/engine.py's `run_scenario` so that what runs in
production is the code the twin tested. The loop itself is unchanged:
model turns, one repair turn for a final answer that isn't JSON, the Tool
Gateway check on every tool call (Epic 20), the artifact's guardrails,
escalation, cooperative cancellation, and suspend/resume at a human
checkpoint (Epic 17).

What differs between the twin and production is behind two ports, plus
the caller's step and phase callbacks:

- `ToolPort` answers a tool call the gateway allowed. The twin's port
  rolls faults and answers from stubs, sample data, an LLM or a sandbox
  API; production's calls the real system.
- `HumanPort` answers a human checkpoint, or returns None to suspend the
  run until a person decides (the twin's manual mode, production's inbox).
- `on_step` / `on_phase` record steps and status. A caller raises
  `RunCancelled` from `on_step` to stop the run at that step.

Grading isn't here: an execution reports what happened, and the twin
grades it afterwards (app/twin/engine.py).

This module must not import from app/twin except the gateway
(tests/test_runtime_executor.py checks it), so twin-only behaviour can't
leak into production.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Protocol

from app.ids import utcnow
from app.llm import ChatMessage, LLMClient, ToolCall, ToolDefinition, Usage, estimate_cost_usd
from app.schemas.agents import AgentDefinition
from app.schemas.run import RunStatus, RunStep, StepType, TwinDeviation
from app.twin.gateway import GuardrailExceeded, GuardrailTracker, check_tool_permission

HUMAN_DECISION_TOOL = ToolDefinition(
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

HumanDecision = Literal["approve", "reject"]


class ExecutionError(Exception):
    """A run can't proceed (an unknown tool, a tool that failed outright, a
    checkpoint rule that can't be evaluated). The run ends FAILED with
    this message. app/twin/errors.py's TwinServiceError is one."""


class RunCancelled(Exception):
    """Epic 17, US17.4: raised by the caller's `on_step` callback the
    moment it observes the run's cancel_requested flag -- caught by the
    turn loop below and turned into status="CANCELLED", the same
    graceful-stop mechanism GuardrailExceeded already uses.

    Epic 39, US39.11: a kill switch raises it from `on_phase`, which runs
    before every model call and tool call, with status "KILLED" and the
    kill's reason."""

    def __init__(self, status: RunStatus = "CANCELLED", reason: str = "Run was cancelled") -> None:
        super().__init__(reason)
        self.status: RunStatus = status
        self.reason = reason


@dataclass
class ToolResult:
    """What a ToolPort returns for one call. `failed` records the step as
    TOOL_FAILED while still giving the model `response` (a simulated 500,
    say). `llm_usage` is (usage, model) when answering the call cost a
    model call of its own, so it counts towards the run's guardrails."""

    response: dict[str, Any]
    failed: bool = False
    reason: str | None = None
    static_fallback: bool = False
    llm_usage: tuple[Usage, str] | None = None


class ToolPort(Protocol):
    async def call(self, system_name: str, call: ToolCall) -> ToolResult:
        """Answers one call the Tool Gateway allowed. Raises ExecutionError
        when the call can't be answered at all."""
        ...


class HumanPort(Protocol):
    async def decide(self, proposed_action: dict[str, Any]) -> HumanDecision | None:
        """A decision now, or None to suspend the run until a person
        decides (the caller then resumes it with `human_decision`)."""
        ...


@dataclass
class ExecutionOutcome:
    status: RunStatus
    steps: list[RunStep] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    # Tool calls the gateway denied: always worth reporting, even when the
    # agent recovered.
    policy_deviations: list[TwinDeviation] = field(default_factory=list)
    # Why a run didn't complete (a limit, a failure, a cancel).
    terminal_deviation: TwinDeviation | None = None
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


_JSON_FENCE = re.compile(r"^\s*```(?:json)?\s*(.*?)\s*```\s*$", re.DOTALL | re.IGNORECASE)


def parse_json_object(text: str | None) -> dict[str, Any] | None:
    if not text:
        return None
    # The final turn runs with tools attached, so JSON mode is off and the
    # model sometimes wraps an otherwise valid object in a ```json fence --
    # found live (every guided-builder run failed on it).
    fenced = _JSON_FENCE.match(text)
    if fenced:
        text = fenced.group(1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def execute(
    llm: LLMClient,
    *,
    artifact: AgentDefinition,
    tools: dict[str, ToolDefinition],
    tool_port: ToolPort,
    human_port: HumanPort,
    inputs: dict[str, Any],
    instructions: str,
    output_fields: list[str],
    max_turns: int,
    operation: str,
    resume_state: dict[str, Any] | None = None,
    human_decision: HumanDecision | None = None,
    node_id: str | None = None,
    on_step: Callable[[RunStep], Awaitable[None]] | None = None,
    on_phase: Callable[[RunStatus], Awaitable[None]] | None = None,
) -> ExecutionOutcome:
    """Runs `artifact` on `inputs` until it gives a final JSON answer, hits
    a limit, is cancelled, or suspends at a human checkpoint.

    `tools` maps each system name to the tool the model is offered for it,
    in the order offered. `instructions` is a system message added after
    the artifact's own prompt for this run only; the stored prompt is
    never changed."""
    tool_name_to_system = {tool.name: system_name for system_name, tool in tools.items()}
    tool_list = list(tools.values())
    has_checkpoint = artifact.human_checkpoint != "none"
    if has_checkpoint:
        tool_list.append(HUMAN_DECISION_TOOL)

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
            ChatMessage(role="system", content=instructions),
            ChatMessage(role="user", content=json.dumps(inputs)),
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
    repaired = False
    terminal_status: RunStatus | None = None
    terminal_deviation: TwinDeviation | None = None

    def suspended(pending: list[ToolCall], turn: int) -> ExecutionOutcome:
        return ExecutionOutcome(
            status="WAITING_FOR_HUMAN",
            steps=steps,
            resume_state={
                "messages": [m.model_dump(mode="json") for m in messages],
                "steps": [s.model_dump(mode="json") for s in steps],
                "tracker": tracker.to_state(),
                "pending_calls": [c.model_dump(mode="json") for c in pending],
                "turn": turn,
            },
        )

    async def process_calls(calls: list[ToolCall], *, decision_for_first: str | None) -> tuple[list[ChatMessage], _Suspend | None]:
        """Resolves `calls` in order, one tool-response ChatMessage per
        call. Stops early (returning the still-unresolved suffix, including
        the call it stopped on) the moment the human port has no decision
        for a checkpoint yet."""
        tool_messages: list[ChatMessage] = []
        first_decision = decision_for_first
        for index, call in enumerate(calls):
            if call.name == HUMAN_DECISION_TOOL.name:
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
                if first_decision is not None:
                    decision = first_decision
                    first_decision = None
                else:
                    decision = await human_port.decide(proposed_action)
                    if decision is None:
                        return tool_messages, _Suspend(pending_calls=calls[index:])
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
                    raise ExecutionError(f"Agent called an unknown tool '{call.name}' that wasn't offered to it")
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
                    try:
                        result = await tool_port.call(system_name, call)
                    except ExecutionError as exc:
                        await emit("TOOL_FAILED", target=system_name, arguments=call.arguments, reason=str(exc))
                        raise
                    tool_response = result.response
                    step_tokens: int | None = None
                    step_cost: float | None = None
                    if result.llm_usage is not None:
                        usage, model = result.llm_usage
                        account(model, usage)
                        step_tokens = usage.total_tokens
                        step_cost = estimate_cost_usd(model, usage)
                        tracker.record_usage(usage.total_tokens, step_cost)
                    await emit(
                        "TOOL_FAILED" if result.failed else "TOOL_EXECUTED",
                        target=system_name, arguments=call.arguments, result=tool_response, reason=result.reason,
                        static_fallback=result.static_fallback, tokens=step_tokens, cost_usd=step_cost,
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
                return suspended(suspend.pending_calls, turns_used)

        for turn in range(next_turn, effective_max_turns + 1):
            turns_used = turn
            await phase("WAITING_FOR_MODEL")
            tracker.before_model_call()
            result = await llm.complete(
                messages,
                operation=operation,
                tools=tool_list or None,
                model=artifact.model,
                response_format=None if tool_list else {"type": "json_object"},
            )
            account(result.model, result.usage)
            model_cost = estimate_cost_usd(result.model, result.usage)
            tracker.record_usage(result.usage.total_tokens, model_cost)
            await emit(
                "MODEL_INVOKED", result={"has_tool_calls": bool(result.tool_calls)},
                tokens=result.usage.total_tokens, cost_usd=model_cost,
            )

            if not result.tool_calls:
                final_output = parse_json_object(result.text)
                if final_output is None and not repaired and turn < effective_max_turns:
                    # Epic 36: one repair turn, as a production runtime would
                    # do -- seen live, 4 of 30 process twin cases ended on a
                    # final answer with prose around the JSON. Recorded as a
                    # RETRY step, so it's visible rather than silently forgiven.
                    repaired = True
                    await emit("RETRY", reason="Final answer was not a valid JSON object; asked once to restate it")
                    keys = f" with exactly these keys: {output_fields}" if output_fields else ""
                    messages.append(ChatMessage(role="assistant", content=result.text or ""))
                    messages.append(ChatMessage(role="user", content=f"That was not a valid JSON object. Reply with ONLY the JSON object{keys}."))
                    continue
                break

            messages.append(ChatMessage(role="assistant", content=result.text or "", tool_calls=result.tool_calls))
            tool_messages, suspend = await process_calls(result.tool_calls, decision_for_first=None)
            messages.extend(tool_messages)
            if suspend is not None:
                return suspended(suspend.pending_calls, turn)
        else:
            terminal_deviation = TwinDeviation(
                reason=f"Exceeded max_steps ({effective_max_turns}) without producing a final answer"
            )
            terminal_status = "ESCALATED" if artifact.escalation_policy.escalate_when else "FAILED"
            if terminal_status == "ESCALATED":
                await emit("ESCALATION", reason=terminal_deviation.reason)
    except RunCancelled as exc:
        terminal_status = exc.status
        terminal_deviation = TwinDeviation(reason=exc.reason)
    except GuardrailExceeded as exc:
        terminal_deviation = TwinDeviation(reason=exc.reason)
        if exc.kind == "timeout":
            terminal_status = "TIMED_OUT"
        else:
            terminal_status = "ESCALATED" if artifact.escalation_policy.escalate_when else "FAILED"
            if terminal_status == "ESCALATED":
                await emit("ESCALATION", reason=exc.reason)
    except ExecutionError as exc:
        terminal_status = "FAILED"
        terminal_deviation = TwinDeviation(reason=str(exc))
    except Exception as exc:  # last-resort net: a background task has nowhere else to send this
        terminal_status = "FAILED"
        terminal_deviation = TwinDeviation(reason=f"Unexpected error: {exc}")

    if terminal_status is None:
        if final_output is None:
            terminal_status = "FAILED"
            terminal_deviation = TwinDeviation(reason="Final response was not a valid JSON object")
        else:
            terminal_status = "COMPLETED"

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

    return ExecutionOutcome(
        status=terminal_status,
        steps=steps,
        final_output=final_output,
        policy_deviations=policy_deviations,
        terminal_deviation=terminal_deviation,
        total_cost_usd=None if cost_incomplete else total_cost,
        total_tokens=total_tokens,
        turns_used=turns_used,
    )
