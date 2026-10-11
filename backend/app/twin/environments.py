"""Epic 21, US21.2: resolves whether a tool call to a given system should
be intercepted by one of its environment's declared faults, and what the
simulated fault response looks like.

Deterministic given a seeded `random.Random` -- reuses the same seeding
convention `app/schemas/twin.py`'s `TwinHumanCheckpointConfig.seed`
already established, so a scenario's fault behavior is reproducible run
to run when a seed is set.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from app.schemas.environment import FaultConfig, FaultType, TwinEnvironment

# US21.2: these fault types represent the call itself failing outright
# (the agent gets an error, not a malformed-but-present response) -- the
# rest simulate a "successful" response that's wrong/incomplete/oddly
# framed in some other way, which the agent has to notice on its own.
_ERROR_FAULT_TYPES: frozenset[FaultType] = frozenset(
    {"timeout", "http_500", "http_429", "permission_denied", "dependency_unavailable"}
)

_DEFAULT_ERROR_MESSAGES: dict[FaultType, str] = {
    "timeout": "Simulated fault: the call to this system timed out.",
    "http_500": "Simulated fault: this system returned an internal server error (500).",
    "http_429": "Simulated fault: this system rate-limited the call (429).",
    "permission_denied": "Simulated fault: this system denied the call (403).",
    "dependency_unavailable": "Simulated fault: a downstream dependency of this system is unavailable.",
}


@dataclass
class FaultOutcome:
    type: FaultType
    result: dict[str, Any]
    is_error: bool


def _build_fault_outcome(fault: FaultConfig) -> FaultOutcome:
    is_error = fault.type in _ERROR_FAULT_TYPES
    if is_error:
        result = {"error": fault.type, "message": fault.config.get("message", _DEFAULT_ERROR_MESSAGES[fault.type])}
    elif fault.type in ("invalid_response", "missing_data", "slow_response"):
        result = dict(fault.config.get("response", {}))
    else:  # duplicate_event, out_of_order_event
        result = {"event_marker": fault.type, **dict(fault.config.get("response", {}))}
    return FaultOutcome(type=fault.type, result=result, is_error=is_error)


def roll_faults(faults: list[FaultConfig], rng: random.Random) -> FaultOutcome | None:
    """Rolls each fault in the order listed, returning the first one whose
    probability hits -- `None` means no fault triggered."""
    for fault in faults:
        if rng.random() < fault.probability:
            return _build_fault_outcome(fault)
    return None


def roll_fault(environment: TwinEnvironment, system_name: str, rng: random.Random) -> FaultOutcome | None:
    """US21.2: rolls each of `system_name`'s declared faults in the order
    they're listed, returning the first one whose probability hits --
    `None` means no fault triggered and the call should be resolved
    normally (Epic 14's existing Proxy/Static mechanism, unaffected)."""
    return roll_faults(environment.faults.get(system_name, []), rng)
