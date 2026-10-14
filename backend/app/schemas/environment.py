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


SystemOperation = Literal["lookup", "create", "update"]


class SimulatedSystemData(BaseModel):
    """Epic 36, US36.1: sample records that stand in for one system during
    a twin run -- a vendor master extract, the open purchase orders, the
    invoice ledger. Agents' calls to the system are answered from these
    records (app/twin/records.py), deterministically and with no LLM call,
    so every case sees the same consistent world and ground truth holds.

    `aliases` let one data set serve the slightly different names agents
    give the same system ("ERP", "SAP S/4HANA ERP"). `lookup_fields` are
    what an agent can search by; `operations` what it may do."""

    description: str = ""
    aliases: list[str] = []
    records: list[dict[str, Any]] = []
    lookup_fields: list[str] = []
    operations: list[SystemOperation] = ["lookup"]
    # Reference prefix for acknowledged writes, e.g. "PAY" -> "PAY-00001".
    reference_prefix: str = "REF"


class TwinEnvironmentCreate(BaseModel):
    name: str
    systems: list[str] = []
    datasets: dict[str, Any] = {}
    personas: dict[str, Any] = {}
    events: list[dict[str, Any]] = []
    faults: dict[str, list[FaultConfig]] = {}
    # Epic 36, US36.1: system name -> sample data the twin answers from.
    system_data: dict[str, SimulatedSystemData] = {}


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
