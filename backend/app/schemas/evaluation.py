"""Epic 19: named evaluation dimensions (US19.1), reusable regression suites
(US19.2/US19.3), and run-vs-run comparison (US19.4) -- replaces today's
single `graded_passed` pass/fail-plus-deviations grading
(`app/twin/engine.py`'s `grade_run`, `app/twin/orchestrator.py`'s
`grade_orchestration`) with twelve independently-visible dimensions, without
touching that grading itself (see `app/twin/evaluation.py`'s module
docstring for why).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

EvaluationDimensionName = Literal[
    "task_completion",
    "policy_compliance",
    "tool_selection_correctness",
    "tool_argument_correctness",
    "output_correctness",
    "grounding",
    "resilience",
    "efficiency",
    "human_intervention",
    "latency",
    "token_usage",
    "cost",
]


class EvaluationDimension(BaseModel):
    name: EvaluationDimensionName
    passed: bool
    detail: str


class EvaluationResult(BaseModel):
    dimensions: list[EvaluationDimension]
    passed_count: int
    total_count: int


class RegressionSuiteCreate(BaseModel):
    name: str
    agent_artifact_id: str | None = None
    # Deliberately not importing RunKind from app.schemas.run here -- run.py
    # imports EvaluationResult from this module, and this is the exact same
    # two-value vocabulary, just declared independently to avoid a cycle.
    kind: Literal["twin", "orchestration"]
    scenario_ids: list[str] = []


class RegressionSuite(RegressionSuiteCreate):
    id: str
    process_id: str
    created_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None


class RegressionSuiteScenarioResult(BaseModel):
    scenario_id: str
    run_id: str
    status: str
    graded_passed: bool | None = None


class RegressionSuiteRun(BaseModel):
    """US19.3: the aggregate result of running a whole suite -- resolved
    entirely from the individual SimulationRun rows the suite's scenarios
    already produce (`app/db/repository.py`'s existing `get_twin_run`/
    `get_orchestration_run`), never a second execution/grading mechanism."""

    id: str
    suite_id: str
    status: Literal["running", "completed"]
    run_ids: list[str]
    pass_count: int
    total_count: int
    scenario_results: list[RegressionSuiteScenarioResult]
    started_at: datetime
    completed_at: datetime | None = None


class RunComparison(BaseModel):
    """US19.4: a plain-English behavioral diff between two runs (typically
    from two different agent versions, US19.5), not a raw side-by-side of
    two traces."""

    run_a_id: str
    run_b_id: str
    differences: list[str]
