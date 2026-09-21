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
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from typing import Any

from app.llm import ChatMessage, LLMClient, ToolDefinition, Usage, estimate_cost_usd
from app.schemas.agents import AgentDefinition
from app.schemas.twin import InferredToolSchema, TwinDeviation, TwinRunStatus, TwinScenario, TwinSystemStub, TwinTraceStep

from .errors import TwinServiceError
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


@dataclass
class TwinRunOutcome:
    status: TwinRunStatus
    trace: list[TwinTraceStep] = field(default_factory=list)
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = field(default_factory=list)
    total_cost_usd: float | None = None
    total_tokens: int = 0
    turns_used: int = 0


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
    trace: list[TwinTraceStep], final_output: dict[str, Any] | None, scenario: TwinScenario
) -> tuple[TwinRunStatus, list[TwinDeviation]]:
    """US14.3: pass/fail is the trace (which simulated systems/checkpoints
    were hit, in what order, with what checkpoint decisions) compared
    pairwise against `expected_steps`, plus the final output compared
    key-by-key against `expected_outputs` -- not an LLM-judge over free
    text (decided in the design discussion this epic's planning doc
    records)."""
    deviations: list[TwinDeviation] = []
    expected = scenario.expected_steps

    for i in range(max(len(expected), len(trace))):
        exp = expected[i] if i < len(expected) else None
        act = trace[i] if i < len(trace) else None
        if exp is None and act is not None:
            deviations.append(
                TwinDeviation(reason=f"Unexpected extra step at index {i}: {act.kind} '{act.target}'", step_index=i)
            )
        elif act is None and exp is not None:
            deviations.append(
                TwinDeviation(
                    reason=f"Missing expected step at index {i}: {exp.kind} '{exp.target or ''}'", step_index=i
                )
            )
        elif exp is not None and act is not None:
            if act.kind != exp.kind or (exp.kind == "tool_call" and act.target != exp.target):
                deviations.append(
                    TwinDeviation(
                        reason=f"Step {i}: expected {exp.kind} '{exp.target}', got {act.kind} '{act.target}'",
                        step_index=i,
                    )
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

    status: TwinRunStatus = "passed" if not deviations else "failed"
    return status, deviations


async def run_scenario(
    llm: LLMClient,
    *,
    artifact: AgentDefinition,
    tool_schemas: dict[str, InferredToolSchema],
    scenario: TwinScenario,
    max_turns: int,
) -> TwinRunOutcome:
    tool_name_to_system = {schema.tool_name: system_name for system_name, schema in tool_schemas.items()}
    tools = [
        ToolDefinition(name=schema.tool_name, description=schema.description, parameters=schema.parameters)
        for schema in tool_schemas.values()
    ]
    has_checkpoint = artifact.human_checkpoint != "none"
    if has_checkpoint:
        tools.append(_HUMAN_DECISION_TOOL)

    output_fields = [f.name for f in artifact.output_schema]
    messages: list[ChatMessage] = [
        ChatMessage(role="system", content=artifact.system_prompt),
        ChatMessage(role="system", content=_twin_instructions(has_checkpoint=has_checkpoint, output_fields=output_fields)),
        ChatMessage(role="user", content=json.dumps(scenario.inputs)),
    ]

    trace: list[TwinTraceStep] = []
    total_tokens = 0
    total_cost = 0.0
    cost_incomplete = False
    rng = random.Random(scenario.human_checkpoint_config.seed)
    final_output: dict[str, Any] | None = None
    turns_used = 0
    loop_deviation: TwinDeviation | None = None

    def account(model: str, usage: Usage) -> None:
        nonlocal total_tokens, total_cost, cost_incomplete
        total_tokens += usage.total_tokens
        cost = estimate_cost_usd(model, usage)
        if cost is None:
            cost_incomplete = True
        else:
            total_cost += cost

    for turn in range(1, max_turns + 1):
        turns_used = turn
        result = await llm.complete(
            messages,
            operation="twin_run",
            tools=tools or None,
            model=artifact.model,
            response_format=None if tools else {"type": "json_object"},
        )
        account(result.model, result.usage)

        if not result.tool_calls:
            final_output = _parse_json_object(result.text)
            break

        messages.append(ChatMessage(role="assistant", content=result.text or "", tool_calls=result.tool_calls))
        for call in result.tool_calls:
            if call.name == _HUMAN_DECISION_TOOL.name:
                proposed_action = call.arguments.get("proposed_action")
                proposed_action = proposed_action if isinstance(proposed_action, dict) else {}
                decision = resolve_human_decision(proposed_action, scenario.human_checkpoint_config, rng)
                trace.append(
                    TwinTraceStep(
                        kind="human_checkpoint", target="human_checkpoint", arguments=call.arguments, decision=decision
                    )
                )
                tool_response: dict[str, Any] = {"decision": decision}
            else:
                system_name = tool_name_to_system.get(call.name)
                if system_name is None:
                    raise TwinServiceError(f"Agent called an unknown tool '{call.name}' that wasn't offered to it")
                schema = tool_schemas[system_name]
                stub = scenario.system_stubs.get(system_name) or TwinSystemStub()
                tool_response, static_fallback, llm_call = await resolve_tool_call(
                    llm, system_name=system_name, call_arguments=call.arguments, schema=schema, stub=stub
                )
                if llm_call is not None:
                    usage, model = llm_call
                    account(model, usage)
                trace.append(
                    TwinTraceStep(
                        kind="tool_call",
                        target=system_name,
                        arguments=call.arguments,
                        result=tool_response,
                        static_fallback=static_fallback,
                    )
                )
            messages.append(
                ChatMessage(role="tool", tool_call_id=call.id, name=call.name, content=json.dumps(tool_response))
            )
    else:
        loop_deviation = TwinDeviation(reason=f"Exceeded max turns ({max_turns}) without producing a final answer")

    status, deviations = grade_run(trace, final_output, scenario)
    if loop_deviation is not None:
        deviations.append(loop_deviation)
        status = "error"
    elif final_output is None:
        deviations.insert(0, TwinDeviation(reason="Final response was not a valid JSON object"))
        status = "error"

    return TwinRunOutcome(
        status=status,
        trace=trace,
        final_output=final_output,
        deviations=deviations,
        total_cost_usd=None if cost_incomplete else total_cost,
        total_tokens=total_tokens,
        turns_used=turns_used,
    )
