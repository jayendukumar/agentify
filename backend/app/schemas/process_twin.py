"""Epic 36: the process digital twin -- the as-is operating model (US36.4),
process twin batches (US36.5) and their efficiency report (US36.6/36.7).
See planning/epics/36-process-twin-efficiency.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.batches.results import Rate


class Triangular(BaseModel):
    """Minutes, as a triangular distribution -- the three numbers a
    process owner can actually give ("usually 8 minutes, never under 5,
    up to 15 on a bad one")."""

    min: float = Field(ge=0)
    likely: float = Field(ge=0)
    max: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "Triangular":
        if not self.min <= self.likely <= self.max:
            raise ValueError("Expected min <= likely <= max")
        return self


class RoleCost(BaseModel):
    role: str
    hourly_cost: float = Field(ge=0)


class NodeOperatingModel(BaseModel):
    """One step of the process, as it runs today and as people stay
    involved in the to-be process."""

    node_id: str
    label: str = ""
    role: str = ""
    # As-is: the person's hands-on time, and the time work waits before it.
    handling_minutes: Triangular | None = None
    wait_minutes: Triangular | None = None
    hourly_cost: float | None = Field(default=None, ge=0)
    # To-be: a reviewer's time when this step's agent asks for review, and
    # the wait before a person picks work up (defaults to the as-is wait).
    review_minutes: Triangular | None = None
    to_be_wait_minutes: Triangular | None = None


class OperatingModel(BaseModel):
    currency: str = "EUR"
    monthly_volume: int = Field(default=1000, ge=0)
    productive_hours_per_fte: float = Field(default=1600, gt=0)
    working_minutes_per_day: float = Field(default=480, gt=0)
    roles: list[RoleCost] = []
    nodes: list[NodeOperatingModel] = []
    # End event id -> the case outcome it means (approve / reject /
    # escalate, or any label the dataset uses).
    end_event_outcomes: dict[str, str] = {}
    # The dataset label compared with the outcome.
    outcome_label: str = "decision"
    # LLM cost is billed in USD; labour cost is in `currency`.
    usd_to_currency: float = Field(default=0.92, gt=0)
    default_review_minutes: Triangular = Triangular(min=1, likely=3, max=6)


class OperatingModelView(OperatingModel):
    process_id: str
    version: int
    is_default: bool = False
    updated_at: datetime | None = None


class ProcessTwinRequest(BaseModel):
    scenario_id: str
    dataset_id: str
    name: str = ""
    sample_size: int | None = Field(default=None, ge=1)
    budget_usd: float | None = Field(default=None, gt=0)
    seed: int = 7


class ProcessTwinEstimate(BaseModel):
    cases: int
    cost_per_case_usd: float
    total_cost_usd: float
    basis: str


class ProcessTwinBatchView(BaseModel):
    id: str
    process_id: str
    name: str
    scenario_id: str
    dataset_id: str
    dataset_version: int
    status: str
    budget_usd: float | None = None
    seed: int
    cases: int
    finished: int
    spent_usd: float
    operating_model_version: int
    created_at: datetime
    finished_at: datetime | None = None


# ---- the efficiency report (US36.6/US36.7) -------------------------------


class CaseEfficiency(BaseModel):
    run_id: str
    row_key: str | None
    segment: str = "typical"
    status: str
    outcome: str | None
    expected_outcome: str | None
    correct: bool | None
    path: list[str]
    straight_through: bool
    human_touches: int
    as_is_cycle_minutes: float
    to_be_cycle_minutes: float
    as_is_touch_minutes: float
    to_be_touch_minutes: float
    as_is_cost: float
    to_be_labour_cost: float
    llm_cost_usd: float
    agent_seconds: float
    failure: str | None = None


class Interval(BaseModel):
    mean: float | None
    low: float | None
    high: float | None


class NodeEfficiency(BaseModel):
    node_id: str
    label: str
    kind: str
    visits: int
    failures: int
    mean_agent_seconds: float | None = None
    review_rate: float | None = None
    mean_llm_cost_usd: float | None = None
    as_is_touch_minutes: float
    to_be_touch_minutes: float


class SegmentEfficiency(BaseModel):
    """US36.7: how each kind of case fares -- a strong average can hide a
    weak segment (duplicates, fraud signals, price variances)."""

    segment: str
    cases: int
    accuracy: Rate
    straight_through: float
    mean_touch_saved_minutes: float


class OutcomeCell(BaseModel):
    expected: str
    actual: str
    count: int


class EfficiencyReport(BaseModel):
    cases: int
    completed: int
    failed: int
    currency: str
    straight_through: Rate
    accuracy: Rate
    as_is_cycle_minutes: Interval
    to_be_cycle_minutes: Interval
    cycle_time_reduction: Interval  # fraction of the as-is cycle time saved
    as_is_touch_minutes: Interval
    to_be_touch_minutes: Interval
    touch_time_reduction: Interval
    as_is_cost_per_case: Interval
    to_be_cost_per_case: Interval  # labour plus LLM, in the model's currency
    llm_cost_per_case_usd: Interval
    annual_hours_released: Interval
    annual_fte_released: Interval
    annual_cost_saved: Interval
    assumptions: list[str]
    headline: str
    nodes: list[NodeEfficiency]
    segments: list[SegmentEfficiency] = []
    outcome_matrix: list[OutcomeCell]
    path_variants: list[dict[str, Any]]
    cases_detail: list[CaseEfficiency]
    usd_to_currency: float = 1.0


ProcessTwinStatus = Literal["running", "paused", "cancelled", "completed", "stopped_budget"]
