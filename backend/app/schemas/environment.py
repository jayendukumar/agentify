"""Epic 21: named, versioned Digital Twin Environments (US21.1) bundling
the systems/datasets/personas/events relevant to a process, plus a typed
fault-injection catalog (US21.2) attachable per system.

Deliberately narrow scope (see planning/epics/21-twin-environments-and-
fault-injection.md's Notes): the environment is where fault-injection
config *lives* and is *catalogued*, not a replacement for Epic 14's
Proxy/Static system-stub simulation mechanism -- a scenario referencing an
environment (US21.3) still resolves a non-faulted call exactly as before
via its own `system_stubs`; the environment's `faults` only intercept a
call *before* that resolution, probabilistically. `systems`/`datasets`/
`personas`/`events` (US21.4) are additive descriptive/reference data with
no other functional wiring in this pass -- there is no "named fault set"
overlay separate from the environment's own `faults` (the epic's Notes
flagged that migration/compatibility shape as undecided; this is the
simpler, additive resolution -- see planning/decision-log.md).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

FaultType = Literal[
    "timeout",
    "http_500",
    "http_429",
    "invalid_response",
    "slow_response",
    "missing_data",
    "duplicate_event",
    "out_of_order_event",
    "permission_denied",
    "dependency_unavailable",
]


class FaultConfig(BaseModel):
    """One fault definition attachable to a system in an environment.
    `config` is fault-type-specific free-form data (e.g. a `response`
    override for invalid_response/missing_data) -- kept as a dict rather
    than ten separate typed variants since most fault types need no extra
    configuration beyond `type` and `probability`."""

    type: FaultType
    probability: float = Field(default=1.0, ge=0, le=1)
    config: dict[str, Any] = {}


class TwinEnvironmentCreate(BaseModel):
    name: str
    systems: list[str] = []
    datasets: dict[str, Any] = {}
    personas: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    faults: dict[str, list[FaultConfig]] = {}


class TwinEnvironmentUpdate(TwinEnvironmentCreate):
    """US21.1: a PUT bumps `version` -- same "single mutable row, version
    int bumped on save" convention as AgentArtifactModel (Epic 22 handles
    true point-in-time reconstruction via per-run snapshots, so this epic
    doesn't need full row history)."""


class TwinEnvironment(TwinEnvironmentCreate):
    id: str
    process_id: str
    version: int
    created_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None
    updated_at: datetime
