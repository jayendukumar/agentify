"""Epic 20: Tool Gateway / Policy Engine.

Checks a twin/orchestration run's tool calls and resource usage against
the artifact's own declared governance (app/schemas/agents.py's
ToolContract/ResourcePermission/RuntimeGuardrails) before app/twin/
simulators.py is ever asked to produce a response, so "the agent may call
this system" is an enforced fact rather than something the system prompt
merely says (FR-SEC-01: "Authorization shall not rely solely on LLM
instructions").

Governance is opt-in per artifact, not retroactively imposed: an artifact
with an empty `tool_contracts` list -- every artifact generated before this
epic, and every hand-built test fixture that doesn't set the new fields --
falls back to Epic 14's original unrestricted behavior (every tool the
model was offered may be called). Once an artifact declares at least one
tool_contract, every tool call is checked against it: a system with no
matching contract, or a contract whose (resource, action) isn't granted in
`permissions`, is denied.

Wired into app/twin/engine.py's run_scenario, the one execution loop both
Epic 14 (single-agent twin runs) and Epic 16 (orchestration rehearsal,
which calls run_scenario once per automatable node) go through -- so both
runtimes are governed by the same gateway without either needing its own
copy of this logic.
"""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Literal

from app.schemas.agents import AgentDefinition, RuntimeGuardrails


@dataclass
class PolicyDecision:
    allowed: bool
    reason: str | None = None


def check_tool_permission(artifact: AgentDefinition, system_name: str) -> PolicyDecision:
    """US20.1/US20.4: is `system_name` a tool this artifact may call right
    now? Fails open (allowed) when the artifact declares no tool contracts
    at all -- see module docstring -- and fails closed (denied) once
    governance is declared: an undeclared tool, or a declared one whose
    resource/action isn't explicitly granted `True` in `permissions`, is
    denied."""
    if not artifact.tool_contracts:
        return PolicyDecision(allowed=True)

    contract = next((c for c in artifact.tool_contracts if c.system_name == system_name), None)
    if contract is None:
        return PolicyDecision(allowed=False, reason=f"'{system_name}' has no declared tool contract on this agent")

    permission = next((p for p in artifact.permissions if p.resource == contract.resource), None)
    granted = bool(permission and permission.actions.get(contract.action))
    if not granted:
        return PolicyDecision(
            allowed=False,
            reason=(
                f"agent lacks '{contract.action}' permission on resource '{contract.resource}' "
                f"(required by tool '{system_name}')"
            ),
        )
    return PolicyDecision(allowed=True)


class GuardrailExceeded(Exception):
    """Raised by GuardrailTracker the moment a configured limit would be
    crossed -- caught by app/twin/engine.py's run loop and turned into a
    graceful TwinDeviation + a terminal RunStatus, never an unhandled
    exception reaching the API layer.

    Epic 17: `kind` lets the engine map this onto the right RunStatus --
    "timeout" (the wall-clock guardrail specifically) always becomes
    TIMED_OUT; every other limit ("limit": tool-call count, tokens, cost,
    loop detection, step exhaustion) becomes ESCALATED when the artifact
    declares an escalation policy, else FAILED (today's behavior)."""

    def __init__(self, reason: str, kind: Literal["timeout", "limit"] = "limit"):
        self.reason = reason
        self.kind = kind
        super().__init__(reason)


@dataclass
class GuardrailTracker:
    """Running counters checked against one artifact's RuntimeGuardrails
    (US20.2) -- replaces app/twin/engine.py's previous single hardcoded
    max_turns check with the full limit set the requirements doc lists
    (steps, tool calls, wall-clock runtime, model calls, tokens, cost,
    and simple loop detection via a retry-attempt ceiling on identical
    calls). `before_*`/`record_usage` raise GuardrailExceeded as soon as a
    call would cross a limit, so a run stops as soon as it becomes
    ungovernable rather than completing one more unaccounted step."""

    guardrails: RuntimeGuardrails
    model_calls: int = 0
    tool_calls: int = 0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    cost_incomplete: bool = False
    _call_counts: "Counter[tuple[str, str]]" = field(default_factory=Counter)
    _started_at: float = field(default_factory=time.monotonic)
    # Epic 17: wall-clock elapsed at the moment a manual human checkpoint
    # suspended a prior leg of this run (0.0 for a run that hasn't been
    # resumed) -- added to this leg's own monotonic elapsed time so
    # max_runtime_seconds is enforced across a suspend/resume gap, not
    # reset by it.
    _prior_elapsed: float = 0.0

    def before_model_call(self) -> None:
        if self.model_calls >= self.guardrails.max_model_calls:
            raise GuardrailExceeded(f"Exceeded max_model_calls ({self.guardrails.max_model_calls})")
        elapsed = self._prior_elapsed + (time.monotonic() - self._started_at)
        if elapsed > self.guardrails.max_runtime_seconds:
            raise GuardrailExceeded(f"Exceeded max_runtime_seconds ({self.guardrails.max_runtime_seconds})", kind="timeout")
        self.model_calls += 1

    def before_tool_call(self, system_name: str, arguments_key: str) -> None:
        if self.tool_calls >= self.guardrails.max_tool_calls:
            raise GuardrailExceeded(f"Exceeded max_tool_calls ({self.guardrails.max_tool_calls})")
        if self.guardrails.loop_detection_enabled:
            key = (system_name, arguments_key)
            self._call_counts[key] += 1
            if self._call_counts[key] > self.guardrails.retry_max_attempts + 1:
                raise GuardrailExceeded(
                    f"Loop detected: '{system_name}' called with identical arguments "
                    f"{self._call_counts[key]} times (retry_max_attempts={self.guardrails.retry_max_attempts})"
                )
        self.tool_calls += 1

    def record_usage(self, tokens: int, cost_usd: float | None) -> None:
        self.total_tokens += tokens
        if cost_usd is None:
            self.cost_incomplete = True
        else:
            self.total_cost_usd += cost_usd
        if self.total_tokens > self.guardrails.max_tokens:
            raise GuardrailExceeded(f"Exceeded max_tokens ({self.guardrails.max_tokens})")
        # Same "unknown beats a wrong partial number" rule engine.py's own
        # cost accounting already follows -- never enforce a cost ceiling
        # against a running total we know is incomplete.
        if not self.cost_incomplete and self.total_cost_usd > self.guardrails.max_cost_usd:
            raise GuardrailExceeded(f"Exceeded max_cost_usd ({self.guardrails.max_cost_usd})")

    def to_state(self) -> dict[str, Any]:
        """Epic 17, US17.3: serializes running counters so a manual human
        checkpoint can suspend mid-run and resume later in a different
        process/request without resetting any guardrail."""
        return {
            "model_calls": self.model_calls,
            "tool_calls": self.tool_calls,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "cost_incomplete": self.cost_incomplete,
            "call_counts": [[list(key), count] for key, count in self._call_counts.items()],
            "elapsed_seconds": self._prior_elapsed + (time.monotonic() - self._started_at),
        }

    @classmethod
    def from_state(cls, guardrails: RuntimeGuardrails, state: dict[str, Any]) -> "GuardrailTracker":
        tracker = cls(
            guardrails=guardrails,
            model_calls=state["model_calls"],
            tool_calls=state["tool_calls"],
            total_tokens=state["total_tokens"],
            total_cost_usd=state["total_cost_usd"],
            cost_incomplete=state["cost_incomplete"],
        )
        tracker._call_counts = Counter({tuple(key): count for key, count in state["call_counts"]})
        tracker._prior_elapsed = state["elapsed_seconds"]
        return tracker
