"""Epic 34: batch execution at volume.

A batch is one data-driven scenario run over (a sample of) a dataset
version, each row several times. Correctness batches put one job per run
on the Epic 31 queue -- retried, bounded by the worker's concurrency,
surviving restarts. Load batches (US34.10) run under one in-process
driver at a target concurrency, so latency under load is what's measured.

Every run gets a seed derived from (batch, row, trial): faults and
probabilistic reviewers are reproducible, and any single run can be
replayed alone (US34.6).
"""

from __future__ import annotations

import hashlib
import random
import statistics
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.datasets import service as datasets
from app.db import repository
from app.db.models import (
    AgentArtifactModel,
    BatchRunModel,
    BatchScheduleModel,
    LabelledDatasetModel,
    SimulationRunModel,
    TwinScenarioModel,
)
from app.ids import new_id, utcnow
from app.jobs import queue
from app.llm import metering
from app.schemas.run import NON_TERMINAL_RUN_STATUSES
from app.store import NotFoundError

Tier = Literal["smoke", "regression", "full", "custom"]
TIER_SIZES = {"smoke": 10, "regression": 300}
_DEFAULT_RUN_COST_USD = 0.01
_DEFAULT_RUN_SECONDS = 30.0


class BatchError(Exception):
    pass


def _now():
    return utcnow().replace(tzinfo=None)


# ---- sampling (US34.8) ----------------------------------------------------


def select_rows(rows: list[Any], *, tier: str, size: int | None, fraction: float | None, seed: int) -> list[Any]:
    """Stratified by segment, reproducible from the seed. Critical rows are
    always included -- the gate (Epic 35) needs every one of them."""
    if tier == "full" and size is None and fraction is None:
        return rows
    target = size if size is not None else (round(len(rows) * fraction) if fraction else TIER_SIZES.get(tier, len(rows)))
    target = max(0, min(target, len(rows)))
    critical = [r for r in rows if r.critical]
    rest = [r for r in rows if not r.critical]
    if target <= len(critical):
        return critical
    by_segment: dict[str, list[Any]] = {}
    for row in rest:
        by_segment.setdefault(row.segment, []).append(row)
    rng = random.Random(seed)
    for segment_rows in by_segment.values():
        rng.shuffle(segment_rows)
    remaining = target - len(critical)
    chosen: list[Any] = []
    # Proportional allocation, at least one per segment while room lasts.
    total = len(rest)
    for segment, segment_rows in sorted(by_segment.items()):
        share = max(1, round(remaining * len(segment_rows) / total)) if total else 0
        chosen.extend(segment_rows[:share])
    if len(chosen) > remaining:
        rng.shuffle(chosen)
        chosen = chosen[:remaining]
    elif len(chosen) < remaining:
        leftovers = [r for r in rest if r not in chosen]
        rng.shuffle(leftovers)
        chosen.extend(leftovers[: remaining - len(chosen)])
    return critical + chosen


# ---- estimates (US34.3) ---------------------------------------------------


class Estimate(BaseModel):
    rows: int
    trials: int
    runs: int
    cost_per_run_usd: float
    seconds_per_run: float
    total_cost_usd: float
    total_minutes: float
    basis: str


def estimate(session: Session, artifact_id: str, *, rows: int, trials: int) -> Estimate:
    """From this agent's recent finished runs; a stated default when it
    has none yet."""
    recent = list(
        session.scalars(
            select(SimulationRunModel)
            .where(SimulationRunModel.agent_artifact_id == artifact_id, SimulationRunModel.completed_at.is_not(None))
            .order_by(SimulationRunModel.started_at.desc())
            .limit(30)
        )
    )
    costs = [r.total_cost_usd for r in recent if r.total_cost_usd is not None]
    durations = [(r.completed_at - r.started_at).total_seconds() for r in recent if r.completed_at and r.started_at]
    cost = statistics.mean(costs) if costs else _DEFAULT_RUN_COST_USD
    seconds = statistics.mean(durations) if durations else _DEFAULT_RUN_SECONDS
    runs = rows * trials
    concurrency = max(1, get_settings().job_concurrency)
    return Estimate(
        rows=rows,
        trials=trials,
        runs=runs,
        cost_per_run_usd=round(cost, 5),
        seconds_per_run=round(seconds, 1),
        total_cost_usd=round(cost * runs, 4),
        total_minutes=round(seconds * runs / concurrency / 60, 1),
        basis=f"average of this agent's last {len(recent)} runs" if recent else "no runs yet -- a default guess",
    )


# ---- starting and controlling batches -------------------------------------


class BatchRequest(BaseModel):
    tier: Tier = "smoke"
    sample_size: int | None = Field(default=None, ge=1)
    sample_fraction: float | None = Field(default=None, gt=0, le=1)
    trials: int = Field(default=1, ge=1, le=10)
    budget_usd: float | None = Field(default=None, gt=0)
    seed: int = 7
    mode: Literal["correctness", "load"] = "correctness"
    target_concurrency: int = Field(default=4, ge=1, le=64)
    stub_latency_ms: int = Field(default=0, ge=0, le=60000)
    keep_traces: Literal["all", "failed_and_sampled"] = "failed_and_sampled"


def run_seed(batch_id: str, row_key: str, trial: int) -> int:
    return int(hashlib.sha256(f"{batch_id}:{row_key}:{trial}".encode()).hexdigest()[:12], 16)


def _scenario(session: Session, process_id: str, scenario_id: str) -> TwinScenarioModel:
    scenario = session.get(TwinScenarioModel, scenario_id)
    if scenario is None:
        raise NotFoundError("scenario", scenario_id)
    artifact = session.get(AgentArtifactModel, scenario.agent_artifact_id)
    if artifact is None or artifact.process_id != process_id:
        raise NotFoundError("scenario", scenario_id)
    if not scenario.dataset_id:
        raise BatchError("This scenario has no dataset -- link one to run it at volume")
    return scenario


def plan(session: Session, process_id: str, scenario_id: str, request: BatchRequest) -> tuple[list[str], Estimate, LabelledDatasetModel]:
    scenario = _scenario(session, process_id, scenario_id)
    dataset = session.get(LabelledDatasetModel, scenario.dataset_id)
    if dataset is None:
        raise BatchError("The scenario's dataset no longer exists")
    rows = [r for r in datasets.rows_at(session, dataset.id) if not r.ambiguous]
    chosen = select_rows(rows, tier=request.tier, size=request.sample_size, fraction=request.sample_fraction, seed=request.seed)
    if not chosen:
        raise BatchError("No usable rows -- the dataset is empty or every row is marked ambiguous")
    return [r.row_key for r in chosen], estimate(session, scenario.agent_artifact_id, rows=len(chosen), trials=request.trials), dataset


def start_batch(
    session: Session,
    process_id: str,
    scenario_id: str,
    request: BatchRequest,
    *,
    created_by: str | None,
    schedule_id: str | None = None,
) -> BatchRunModel:
    scenario = _scenario(session, process_id, scenario_id)
    row_keys, est, dataset = plan(session, process_id, scenario_id, request)
    artifact = session.get(AgentArtifactModel, scenario.agent_artifact_id)
    batch = BatchRunModel(
        id=new_id("batch"),
        process_id=process_id,
        scenario_id=scenario_id,
        agent_artifact_id=artifact.id,
        agent_version=artifact.version,
        dataset_id=dataset.id,
        dataset_version=dataset.version,
        mode=request.mode,
        tier=request.tier,
        trials=request.trials,
        sample={"size": request.sample_size, "fraction": request.sample_fraction, "seed": request.seed},
        row_keys=row_keys,
        status="running",
        budget_usd=request.budget_usd,
        estimate=est.model_dump(),
        load_settings={"target_concurrency": request.target_concurrency, "stub_latency_ms": request.stub_latency_ms}
        if request.mode == "load"
        else {},
        keep_traces=request.keep_traces,
        schedule_id=schedule_id,
        created_by=created_by,
    )
    session.add(batch)
    session.flush()
    run_ids = []
    for trial in range(1, request.trials + 1):
        for row_key in row_keys:
            run = repository.start_twin_run(
                session, process_id, scenario_id, run_by=created_by, row_key=row_key,
                dataset_version=dataset.version, trial=trial, batch_id=batch.id,
            )
            model = session.get(SimulationRunModel, run.id)
            model.seed = run_seed(batch.id, row_key, trial)
            run_ids.append(run.id)
    session.flush()
    if request.mode == "load":
        queue.enqueue(session, "load_batch", {"batch_id": batch.id}, max_attempts=1, dedupe_key=f"load:{batch.id}")
    else:
        for run_id in run_ids:
            queue.enqueue(session, "twin_run", {"run_id": run_id}, max_attempts=2)
    session.flush()
    return batch


def get_batch(session: Session, process_id: str, batch_id: str) -> BatchRunModel:
    batch = session.get(BatchRunModel, batch_id)
    if batch is None or batch.process_id != process_id:
        raise NotFoundError("batch", batch_id)
    return batch


def batch_runs(session: Session, batch_id: str) -> list[SimulationRunModel]:
    return list(session.scalars(select(SimulationRunModel).where(SimulationRunModel.batch_id == batch_id)))


def spent(session: Session, batch_id: str) -> float:
    return round(sum(r.total_cost_usd or 0 for r in batch_runs(session, batch_id)), 6)


def _cancel_pending(session: Session, batch: BatchRunModel, reason: str) -> int:
    count = 0
    for run in batch_runs(session, batch.id):
        if run.status == "CREATED":
            run.status = "CANCELLED"
            run.deviations = [{"reason": reason}]
            run.completed_at = _now()
            count += 1
    session.flush()
    return count


def set_status(session: Session, batch: BatchRunModel, action: Literal["pause", "resume", "cancel"]) -> BatchRunModel:
    """US34.7."""
    if batch.status in ("completed", "cancelled", "stopped_budget"):
        raise BatchError(f"The batch is already {batch.status.replace('_', ' ')}")
    if action == "pause":
        batch.status = "paused"
    elif action == "resume":
        batch.status = "running"
    else:
        batch.status = "cancelled"
        _cancel_pending(session, batch, "Batch was cancelled")
        batch.finished_at = _now()
    session.flush()
    return batch


def gate_run(session: Session, run: SimulationRunModel) -> Literal["go", "wait", "skip"]:
    """Checked before a batch run executes: honours pause, cancel, and the
    budget cap (US34.3)."""
    batch = session.get(BatchRunModel, run.batch_id) if run.batch_id else None
    if batch is None:
        return "go"
    if run.status != "CREATED":
        return "skip"
    if batch.status == "paused":
        return "wait"
    if batch.status in ("cancelled", "stopped_budget", "completed"):
        run.status = "CANCELLED"
        run.deviations = [{"reason": f"Batch {batch.status.replace('_', ' ')}"}]
        run.completed_at = _now()
        session.flush()
        return "skip"
    if batch.budget_usd is not None and spent(session, batch.id) >= batch.budget_usd:
        batch.status = "stopped_budget"
        batch.finished_at = _now()
        _cancel_pending(session, batch, f"Budget of ${batch.budget_usd:.2f} reached")
        return "skip"
    if metering.budget_reached(session):  # Epic 38, US38.9: the tenant's month
        batch.status = "stopped_budget"
        batch.finished_at = _now()
        _cancel_pending(session, batch, "This month's LLM budget is used up")
        return "skip"
    return "go"


def finish_if_done(session: Session, batch_id: str | None) -> None:
    """Marks the batch completed once no run is in flight, then prunes
    traces it doesn't need to keep (Notes: full traces for failed, flaky,
    and a sample of passing runs)."""
    if batch_id is None:
        return
    batch = session.get(BatchRunModel, batch_id)
    if batch is None:
        return
    runs = batch_runs(session, batch_id)
    if any(r.status in NON_TERMINAL_RUN_STATUSES for r in runs):
        return
    if batch.status in ("running", "paused"):
        batch.status = "completed"
        batch.finished_at = _now()
    if batch.keep_traces == "failed_and_sampled":
        flaky = _flaky_rows(runs)
        rng = random.Random(batch.id)
        for run in runs:
            keep = run.graded_passed is not True or run.row_key in flaky or rng.random() < 0.1
            if not keep and run.steps:
                run.steps = []
    session.flush()


def _flaky_rows(runs: list[SimulationRunModel]) -> set[str]:
    outcomes: dict[str, set[bool]] = {}
    for run in runs:
        if run.row_key and run.graded_passed is not None:
            outcomes.setdefault(run.row_key, set()).add(run.graded_passed)
    return {key for key, seen in outcomes.items() if len(seen) > 1}


class BatchView(BaseModel):
    id: str
    scenario_id: str
    agent_artifact_id: str
    agent_version: int
    dataset_id: str
    dataset_version: int
    mode: str
    tier: str
    trials: int
    rows: int
    status: str
    budget_usd: float | None = None
    spent_usd: float
    estimate: dict[str, Any]
    load_settings: dict[str, Any] = {}
    progress: dict[str, int]
    created_at: Any
    finished_at: Any = None


def to_view(session: Session, batch: BatchRunModel) -> BatchView:
    runs = batch_runs(session, batch.id)
    progress = {
        "total": len(runs),
        "finished": sum(1 for r in runs if r.status not in NON_TERMINAL_RUN_STATUSES),
        "passed": sum(1 for r in runs if r.graded_passed is True),
        "failed": sum(1 for r in runs if r.graded_passed is False),
        "errored": sum(1 for r in runs if r.status in ("FAILED", "TIMED_OUT")),
        "cancelled": sum(1 for r in runs if r.status == "CANCELLED"),
    }
    return BatchView(
        id=batch.id,
        scenario_id=batch.scenario_id,
        agent_artifact_id=batch.agent_artifact_id,
        agent_version=batch.agent_version,
        dataset_id=batch.dataset_id,
        dataset_version=batch.dataset_version,
        mode=batch.mode,
        tier=batch.tier,
        trials=batch.trials,
        rows=len(batch.row_keys),
        status=batch.status,
        budget_usd=batch.budget_usd,
        spent_usd=round(sum(r.total_cost_usd or 0 for r in runs), 6),
        estimate=batch.estimate,
        load_settings=batch.load_settings,
        progress=progress,
        created_at=batch.created_at,
        finished_at=batch.finished_at,
    )


def replay(session: Session, batch: BatchRunModel, run_id: str, *, run_by: str | None) -> str:
    """US34.6: re-run one row alone with the same seed."""
    original = session.get(SimulationRunModel, run_id)
    if original is None or original.batch_id != batch.id:
        raise NotFoundError("run", run_id)
    run = repository.start_twin_run(
        session, batch.process_id, batch.scenario_id, run_by=run_by, row_key=original.row_key,
        dataset_version=original.dataset_version, trial=original.trial,
    )
    session.get(SimulationRunModel, run.id).seed = original.seed
    session.flush()
    return run.id


# ---- schedules (US34.9) ---------------------------------------------------


class ScheduleCreate(BaseModel):
    scenario_id: str
    tier: Tier = "full"
    trials: int = Field(default=1, ge=1, le=10)
    every_minutes: int | None = Field(default=None, ge=15)
    on_dataset_change: bool = True
    on_context_change: bool = True
    budget_usd: float | None = Field(default=None, gt=0)
    enabled: bool = True


def schedule_due(session: Session, schedule: BatchScheduleModel) -> str | None:
    """Why this schedule should run now, or None."""
    from datetime import timedelta

    scenario = session.get(TwinScenarioModel, schedule.scenario_id)
    if scenario is None or not scenario.dataset_id:
        return None
    dataset = session.get(LabelledDatasetModel, scenario.dataset_id)
    artifact = session.get(AgentArtifactModel, scenario.agent_artifact_id)
    if dataset is None or artifact is None:
        return None
    active = session.scalar(
        select(BatchRunModel).where(BatchRunModel.schedule_id == schedule.id, BatchRunModel.status.in_(("running", "paused")))
    )
    if active is not None:
        return None
    if schedule.every_minutes and (
        schedule.last_run_at is None or schedule.last_run_at + timedelta(minutes=schedule.every_minutes) <= _now()
    ):
        return "scheduled"
    if schedule.on_dataset_change and schedule.last_dataset_version is not None and dataset.version != schedule.last_dataset_version:
        return "dataset changed"
    if schedule.on_context_change and schedule.last_context_pack_id is not None:
        from app.context.resolver import is_pack_stale

        if artifact.context_pack_id != schedule.last_context_pack_id or is_pack_stale(session, artifact.context_pack_id):
            return "organization context changed"
    return None


def run_schedule(session: Session, schedule: BatchScheduleModel, reason: str) -> BatchRunModel:
    batch = start_batch(
        session, schedule.process_id, schedule.scenario_id,
        BatchRequest(tier=schedule.tier, trials=schedule.trials, budget_usd=schedule.budget_usd),  # type: ignore[arg-type]
        created_by=None, schedule_id=schedule.id,
    )
    scenario = session.get(TwinScenarioModel, schedule.scenario_id)
    artifact = session.get(AgentArtifactModel, scenario.agent_artifact_id)
    schedule.last_run_at = _now()
    schedule.last_dataset_version = batch.dataset_version
    schedule.last_context_pack_id = artifact.context_pack_id
    batch.estimate = {**batch.estimate, "trigger": reason}
    session.flush()
    return batch


def enqueue_due_batches(session: Session) -> int:
    count = 0
    for schedule in session.scalars(select(BatchScheduleModel).where(BatchScheduleModel.enabled.is_(True))):
        reason = schedule_due(session, schedule)
        if reason is None:
            continue
        try:
            run_schedule(session, schedule, reason)
            count += 1
        except (BatchError, NotFoundError):
            schedule.enabled = False  # the scenario or dataset is gone; stop retrying every tick
    session.flush()
    return count
