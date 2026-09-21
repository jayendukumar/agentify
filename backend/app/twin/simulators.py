"""Epic 14 (core slice): resolves what a simulated system or a simulated
human returns during a twin run, per the human/system/agent model in
planning/epics/14-digital-twin-simulation.md's "Discovery" section.

System modes (US14.2): Proxy (LLM fabricates a plausible response against
the inferred schema) and Static (scenario-provided input/output table,
falling back to Proxy if nothing matches -- not a hard error, since a
scenario author may only care about stubbing a few specific calls).
API mode (a real dev-provided endpoint) is deliberately out of scope for
this slice.

Human-checkpoint modes: probability (sampled, optionally seeded for
repeatable runs) and rule (a single condition over the agent's proposed
action). Manual (pause-and-wait-for-a-real-person) is deliberately out of
scope for this slice -- it needs pause/resume run state that doesn't exist
in this backend yet.
"""

from __future__ import annotations

import json
import random
from typing import Any, Literal

from app.llm import ChatMessage, LLMClient, Usage
from app.schemas.twin import InferredToolSchema, TwinHumanCheckpointConfig, TwinSystemStub

from .errors import TwinServiceError

_OPERATORS = {
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
}


def _matches(match: dict[str, Any], arguments: dict[str, Any]) -> bool:
    return all(key in arguments and arguments[key] == value for key, value in match.items())


async def _fabricate_proxy_response(
    llm: LLMClient, system_name: str, schema: InferredToolSchema, call_arguments: dict[str, Any]
) -> tuple[dict[str, Any], Usage, str]:
    prompt = (
        f"You are simulating the system \"{system_name}\" for testing purposes only -- this is not a "
        "real call. A tool named "
        f'"{schema.tool_name}" ({schema.description}) was just called with these arguments: '
        f"{json.dumps(call_arguments)}\n\n"
        f"A typical successful response looks like: {schema.response_shape_description}\n\n"
        "Return ONLY a JSON object that is a plausible response to this specific call."
    )
    result = await llm.complete(
        [ChatMessage(role="system", content=prompt)],
        operation="twin_system_simulation",
        response_format={"type": "json_object"},
        max_tokens=1000,
    )
    if not result.text:
        raise TwinServiceError(f"LLM returned no content while simulating a response from '{system_name}'")
    try:
        response = json.loads(result.text)
    except json.JSONDecodeError as exc:
        raise TwinServiceError(f"LLM returned invalid JSON while simulating a response from '{system_name}': {exc}") from exc
    if not isinstance(response, dict):
        raise TwinServiceError(f"LLM's simulated response from '{system_name}' was not a JSON object")
    return response, result.usage, result.model


async def resolve_tool_call(
    llm: LLMClient,
    *,
    system_name: str,
    call_arguments: dict[str, Any],
    schema: InferredToolSchema,
    stub: TwinSystemStub,
) -> tuple[dict[str, Any], bool, tuple[Usage, str] | None]:
    """Returns (response, static_fallback_used, (usage, model)_if_an_llm_call_was_made)."""
    if stub.mode == "static":
        for rule in stub.static_responses:
            if _matches(rule.match, call_arguments):
                return rule.response, False, None
        # No configured rule matched this call -- fall back to Proxy rather
        # than hard-failing the run, since a scenario author may only care
        # about stubbing a handful of specific calls.
        response, usage, model = await _fabricate_proxy_response(llm, system_name, schema, call_arguments)
        return response, True, (usage, model)

    response, usage, model = await _fabricate_proxy_response(llm, system_name, schema, call_arguments)
    return response, False, (usage, model)


def resolve_human_decision(
    proposed_action: dict[str, Any], config: TwinHumanCheckpointConfig, rng: random.Random
) -> Literal["approve", "reject"]:
    if config.mode == "probability":
        return "approve" if rng.random() < config.approve_probability else "reject"

    rule = config.rule
    if rule is None:
        raise TwinServiceError("Scenario's human_checkpoint_config is mode='rule' but no rule was configured")
    if rule.field not in proposed_action:
        raise TwinServiceError(
            f"Human-checkpoint rule references field '{rule.field}', which the agent's proposed_action "
            f"did not include (got: {sorted(proposed_action)})"
        )

    condition = _OPERATORS[rule.operator](proposed_action[rule.field], rule.value)
    return rule.on_true if condition else rule.on_false
