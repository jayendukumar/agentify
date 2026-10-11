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

from .environment import FaultConfig
from .run import RunBase, TwinDeviation

# Epic 29, US29.5: "api" calls the linked application's registered
# non-production endpoint (app/catalog/endpoints.py refuses anything else).
SystemStubMode = Literal["proxy", "static", "api"]
# Epic 17, US17.3: "manual" is new -- a real person supplies the checkpoint
# decision later via the run's resume endpoint, instead of the engine
# auto-resolving it from `approve_probability`/`rule` the moment the agent
# asks. See app/twin/engine.py's suspend path.
HumanCheckpointMode = Literal["probability", "rule", "manual"]
RuleOperator = Literal["eq", "ne", "gt", "gte", "lt", "lte"]
# Epic 17: kept narrow and separate from app/schemas/run.py's full StepType
# taxonomy -- this is only the two-kind vocabulary a scenario author uses to
# declare an *expected* step (US14.3's grading), not the runtime's own
# sixteen-value execution trace.
TraceStepKind = Literal["tool_call", "human_checkpoint"]
# Epic 24, US24.4: "exact" is Epic 14's original pairwise comparison (every
# actual step must line up with an expected one, extras are deviations);
# "in_order" only requires the expected steps to appear as an in-order
# subsequence of the actual ones -- what a business user can actually
# state ("the CRM gets checked, then the manager is asked") without having
# to predict every retry or extra lookup the agent makes.
StepMatchMode = Literal["exact", "in_order"]


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


CoverageKind = Literal[
    "happy_path", "gateway_branch", "checkpoint", "system_failure", "data_quality", "custom",
    # Epic 30, US30.1: one target per applicable, testable control.
    "control",
]
# Epic 30, US30.5: what a control demands, checkable from the run trace.
ComplianceAssertionKind = Literal["escalated", "blocked", "logged", "reviewed"]
SystemBehaviour = Literal["normal", "down", "timeout", "denied", "bad_data", "no_record", "custom_data"]
ReviewerBehaviour = Literal["none", "approve", "reject", "threshold"]


class CoverageTarget(BaseModel):
    """Epic 24, US24.5: one thing about an agent worth testing, derived
    deterministically from its blueprint (never by the LLM) -- the
    denominator of the coverage meter. `id` is stable for a given artifact
    + baseline so a saved scenario's `covers` keeps pointing at the same
    target across page loads."""

    id: str
    kind: CoverageKind
    label: str


class ReviewerSetup(BaseModel):
    behaviour: ReviewerBehaviour = "none"
    field: str | None = None
    threshold: float | None = None
    persona_name: str | None = None


class ComplianceAssertion(BaseModel):
    """Epic 30, US30.5: one control behaviour a situation asserts.

    - escalated: the agent handed the case to a person -- asked for a human
      decision, or the run ended ESCALATED. With `role`, the request or the
      final output must name that role.
    - blocked: the agent did NOT carry out the action on `system`.
    - reviewed: a person was asked before the action on `system` (or before
      any action, without `system`).
    - logged: the final output records the decision with a non-empty
      explanation (`field`, or any of explanation/reason/rationale).
    """

    control_id: str
    kind: ComplianceAssertionKind
    role: str | None = None
    system: str | None = None
    field: str | None = None


class SituationExpectation(BaseModel):
    systems_used: list[str] = []
    review_decision: Literal["approve", "reject"] | None = None
    output_fields: list[str] = []
    compliance: list[ComplianceAssertion] = []


class ScenarioStory(BaseModel):
    """Epic 24, US24.6: the business-readable form a guided-builder
    scenario was authored in, persisted alongside the compiled
    engine-facing fields so the UI can always re-render (and re-edit) the
    story rather than the JSON. Purely descriptive at execution time --
    the engine only reads the compiled fields."""

    title: str
    given: str = ""
    when: str = ""
    then: str = ""
    covers: list[str] = []
    source: Literal["suggested", "described", "manual"] = "manual"
    system_behaviour: dict[str, SystemBehaviour] = {}
    system_data: dict[str, dict[str, Any]] = {}
    reviewer: ReviewerSetup = ReviewerSetup()
    expect: SituationExpectation = SituationExpectation()


class TwinScenarioCreate(BaseModel):
    name: str
    inputs: dict[str, Any] = {}
    system_stubs: dict[str, TwinSystemStub] = {}
    human_checkpoint_config: TwinHumanCheckpointConfig = TwinHumanCheckpointConfig()
    expected_steps: list[TwinExpectedStep] = []
    expected_outputs: dict[str, Any] = {}
    # Epic 21, US21.3: optionally pins this scenario to a reusable
    # TwinEnvironment whose fault catalog intercepts calls before
    # `system_stubs` resolution -- see app/schemas/environment.py.
    environment_id: str | None = None
    environment_version: int | None = None
    # Epic 24, US24.6: per-scenario faults, offered before the
    # environment's own catalog -- lets "the ERP is down" be part of the
    # situation itself rather than needing a dedicated environment.
    faults: dict[str, list[FaultConfig]] = {}
    step_match: StepMatchMode = "exact"
    story: ScenarioStory | None = None
    # Epic 32, US32.2: a data-driven scenario -- run once per dataset row,
    # each graded against that row's labels. `decision_field` is the
    # agent output field a row's decision label is compared with.
    dataset_id: str | None = None
    decision_field: str = "decision"
    # Set per run from the row's label (never stored on the scenario).
    expected_decision: Literal["approve", "reject", "escalate"] | None = None
    # Epic 34, US34.10: simulated system latency for load batches (per run,
    # never stored).
    stub_latency_ms: int = 0


class TwinScenario(TwinScenarioCreate):
    id: str
    agent_artifact_id: str
    created_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None


class TwinRun(RunBase):
    """Epic 17: a twin run is now one `kind="twin"` SimulationRun (see
    app/db/models.py) -- this schema is the API-facing projection of it,
    extending app/schemas/run.py's RunBase with the two fields that only
    make sense for a single-agent run (an orchestration run instead has
    `process_id`/`node_runs`/`visited_path` -- see app/schemas/
    orchestration.py's OrchestrationRun)."""

    scenario_id: str
    agent_artifact_id: str
    turns_used: int = 0
    # Epic 32/34: the dataset row (pinned version) this run executed.
    dataset_id: str | None = None
    dataset_version: int | None = None
    row_key: str | None = None
    trial: int | None = None
    batch_id: str | None = None


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
    # Epic 29, US29.4/US29.5: "openapi" when built from the linked
    # application's real spec instead of inferred by the LLM; `api_*` is
    # what API mode needs to make the call.
    source: Literal["inferred", "openapi"] = "inferred"
    application_id: str | None = None
    api_base_url: str | None = None
    api_environment: str | None = None
    api_operations: dict[str, dict[str, str]] = {}


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
