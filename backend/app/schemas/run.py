"""Epic 17: the unified run state machine and typed execution-step taxonomy
shared by twin runs (Epic 14) and orchestration runs (Epic 16) -- replaces
the two independent, ad hoc shapes each epic built on its own (Twin's flat
`passed|failed|error` status over a two-kind trace; Orchestration's
`running|passed|failed|error` status over per-node `NodeRun`s). See
planning/epics/17-simulation-run-lifecycle.md for the full design.

`RunStatus` deliberately describes execution/lifecycle outcome only --
never scenario grading (did the trace/output match what a scenario
expected). A run that executes cleanly is always `COMPLETED`, even if
grading found deviations (that's `graded_passed=False` on the run itself,
computed independently) -- otherwise "what does this status mean" would
still depend on which kind of run you're looking at, defeating the point
of unifying them (US17.1).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from .evaluation import EvaluationResult

RunKind = Literal["twin", "orchestration"]

RunStatus = Literal[
    "CREATED",
    "QUEUED",
    "RUNNING",
    "WAITING_FOR_MODEL",
    "WAITING_FOR_TOOL",
    "WAITING_FOR_HUMAN",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "TIMED_OUT",
    "ESCALATED",
]

# US17.1's non-terminal states -- a row in any of these is still in flight.
NON_TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {"CREATED", "QUEUED", "RUNNING", "WAITING_FOR_MODEL", "WAITING_FOR_TOOL", "WAITING_FOR_HUMAN"}
)
TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT", "ESCALATED"}
)

# US17.2's fifteen-... sixteen-value step taxonomy, replacing the old
# tool_call/human_checkpoint-only TraceStepKind.
StepType = Literal[
    "RUN_STARTED",
    "CONTEXT_RETRIEVED",
    "MODEL_INVOKED",
    "DECISION",
    "TOOL_REQUESTED",
    "POLICY_CHECK",
    "TOOL_EXECUTED",
    "TOOL_FAILED",
    "HUMAN_APPROVAL_REQUESTED",
    "HUMAN_APPROVED",
    "HUMAN_REJECTED",
    "AGENT_DELEGATED",
    "RETRY",
    "ESCALATION",
    "FINAL_RESPONSE",
    "RUN_COMPLETED",
]


class TwinDeviation(BaseModel):
    """A single mismatch between what a scenario expected and what a run
    actually did (or, for a guardrail/execution failure, a plain
    explanation) -- one shape reused by both twin and orchestration
    grading, named for the epic that introduced it, not kind-specific."""

    reason: str
    step_index: int | None = None


class RunStep(BaseModel):
    """One typed event in a run's execution trace. `node_id` tags which
    orchestration node this step happened inside (Epic 16); it's always
    None for a standalone twin run (Epic 14) and for an orchestration run's
    own walk-level events (gateway decisions, node delegation).

    Epic 18, US18.2: `tokens`/`cost_usd` are the LLM usage *this specific
    step* caused (a MODEL_INVOKED turn, a proxy-simulated TOOL_EXECUTED
    response, or an AGENT_DELEGATED data-adapter reshape) -- None for a
    step with no LLM call of its own (e.g. a static-mode tool response, a
    human decision), and always None on a step mapped from a pre-Epic-18
    historical row (app/db/repository.py's `_legacy_trace_to_steps`,
    which never recorded this). Run-level `total_tokens`/`total_cost_usd`
    (RunBase below) remain the authoritative aggregate; these are additive
    detail for the step inspector, not a second source of truth to keep in
    sync by hand -- nothing sums these to produce the run-level total."""

    seq: int
    type: StepType
    node_id: str | None = None
    target: str | None = None
    arguments: dict[str, Any] = {}
    result: dict[str, Any] | None = None
    decision: Literal["approve", "reject"] | None = None
    reason: str | None = None
    denied: bool = False
    static_fallback: bool = False
    tokens: int | None = None
    cost_usd: float | None = None
    occurred_at: datetime


class ResumeDecision(BaseModel):
    """Epic 17, US17.3: the request body for resuming a WAITING_FOR_HUMAN
    run -- a real person's answer to the pending manual human checkpoint."""

    decision: Literal["approve", "reject"]


class RunBase(BaseModel):
    id: str
    kind: RunKind
    status: RunStatus
    steps: list[RunStep] = []
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = []
    graded_passed: bool | None = None
    # Epic 19, US19.1: computed once alongside graded_passed (see
    # app/twin/evaluation.py) -- None for a non-terminal or ungraded run,
    # and for every run persisted before this epic.
    evaluation: EvaluationResult | None = None
    total_cost_usd: float | None = None
    total_tokens: int = 0
    started_at: datetime
    completed_at: datetime | None = None
    run_by: str | None = None
    run_by_name: str | None = None
