"""Epic 14: scenario definitions and run results for digital twin
simulation of a generated agent artifact (Epic 12) -- US14.1-14.6. See
planning/epics/14-digital-twin-simulation.md's "Discovery" section for the
human/system/agent simulation model this schema implements.

Deliberately out of scope here (see the same planning doc): API mode
(dev-provided real endpoint) for systems, and manual (pause-and-wait-for-a-
real-person) mode for human checkpoints -- both need architecture this
epic's core slice didn't build (a real external contract for the former,
pause/resume run state for the latter).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

SystemStubMode = Literal["proxy", "static"]
HumanCheckpointMode = Literal["probability", "rule"]
RuleOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]
TraceStepKind = Literal["tool_call", "human_checkpoint"]
TwinRunStatus = Literal["passed", "failed", "error"]


class StaticResponseRule(BaseModel):
    """One row of a Static-mode input/output table: `match` is a subset
    match against the tool call's arguments (empty dict matches any call),
    `response` is returned verbatim as the simulated tool result for the
    first matching rule."""

    match: dict[str, Any] = {}
    response: dict[str, Any] = {}


class TwinSystemStub(BaseModel):
    """Per-system-name simulation config for one scenario. Systems not
    listed here default to Proxy mode with no static override."""

    mode: SystemStubMode = "proxy"
    static_responses: list[StaticResponseRule] = []


class HumanDecisionRule(BaseModel):
    """A single condition evaluated against the agent's `proposed_action`
    payload when it calls the synthetic request_human_decision tool --
    deliberately one condition, not a full expression language, for this
    first slice."""

    field: str
    operator: RuleOperator
    value: Any
    on_true: Literal["approve", "reject"]
    on_false: Literal["approve", "reject"]


class TwinHumanCheckpointConfig(BaseModel):
    mode: HumanCheckpointMode = "probability"
    approve_probability: float = 1.0
    seed: int | None = None
    rule: HumanDecisionRule | None = None


class TwinExpectedStep(BaseModel):
    """One entry of a scenario's expected tool-call/checkpoint path.
    `target` is a system name for kind="tool_call", or unused for
    kind="human_checkpoint". `expected_decision` only applies to
    kind="human_checkpoint"."""

    kind: TraceStepKind
    target: str | None = None
    expected_decision: Literal["approve", "reject"] | None = None


class TwinScenarioCreate(BaseModel):
    name: str
    inputs: dict[str, Any] = {}
    system_stubs: dict[str, TwinSystemStub] = {}
    human_checkpoint_config: TwinHumanCheckpointConfig = TwinHumanCheckpointConfig()
    expected_steps: list[TwinExpectedStep] = []
    expected_outputs: dict[str, Any] = {}


class TwinScenario(TwinScenarioCreate):
    id: str
    agent_artifact_id: str
    created_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None


class TwinTraceStep(BaseModel):
    kind: TraceStepKind
    target: str
    arguments: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    decision: Literal["approve", "reject"] | None = None
    static_fallback: bool = False


class TwinDeviation(BaseModel):
    reason: str
    step_index: int | None = None


class TwinRun(BaseModel):
    id: str
    scenario_id: str
    agent_artifact_id: str
    status: TwinRunStatus
    trace: list[TwinTraceStep]
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation]
    total_cost_usd: float | None = None
    total_tokens: int
    turns_used: int
    started_at: datetime
    completed_at: datetime
    run_by: str | None = None
    run_by_name: str | None = None


class InferredToolSchema(BaseModel):
    """One synthesized, callable schema for a `tools_systems_needed` entry
    -- resolves the gap left by Epic 12 (that field is plain strings, not
    anything callable). Mode-independent: both Proxy and Static system
    stubs use this same schema, only how a call's response gets produced
    differs (app/twin/simulators.py)."""

    system_name: str
    tool_name: str
    description: str
    parameters: dict[str, Any]
    response_shape_description: str


class TwinBaselineInput(BaseModel):
    """US14.5: what an Automation Architect supplies by hand for the as-is
    (manual, pre-automation) version of the step -- typical time to
    complete one instance, and its error rate. Both optional and
    independent (an architect may only know one of the two). There is no
    automatic extraction of this anywhere in the system -- the process
    schema (Epics 1/2) captures no timing/error-rate data, a real gap
    noted in this epic's planning doc -- so this is explicitly a
    user-supplied estimate, never something the system fabricates."""

    typical_time_seconds: float | None = Field(default=None, ge=0)
    error_rate: float | None = Field(default=None, ge=0, le=1)
    notes: str | None = None


class TwinBaseline(TwinBaselineInput):
    agent_artifact_id: str
    recorded_at: datetime
    recorded_by: str | None = None
    recorded_by_name: str | None = None


class TwinBaselineComparison(BaseModel):
    """Only populated where both a baseline value and a twin-run
    equivalent exist -- a missing side means "not comparable", not zero.
    A negative `time_delta_seconds` means the twin ran faster than the
    manual baseline; a negative `error_rate_delta` means the twin failed
    less often."""

    average_run_duration_seconds: float | None = None
    time_delta_seconds: float | None = None
    error_rate_delta: float | None = None


class TwinSummary(BaseModel):
    agent_artifact_id: str
    run_count: int
    pass_rate: float | None = None
    total_cost_usd: float | None = None
    average_cost_usd: float | None = None
    common_failure_reasons: list[str] = []
    baseline: TwinBaseline | None = None
    baseline_comparison: TwinBaselineComparison | None = None
