"""Epic 34: batch execution at volume -- estimate, start, control, replay,
and schedule data-driven scenario batches."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from app.batches import service
from app.config import get_settings
from app.db.models import AgentArtifactModel, BatchRunModel, BatchScheduleModel, LabelledDatasetModel, TwinScenarioModel
from app.ids import new_id
from app.jobs.worker import run_pending
from app.store import NotFoundError
from app.twin.errors import TwinServiceError

from .deps import AgentTestDep, CurrentUserDep, DbDep

router = APIRouter(prefix="/api/processes/{process_id}", tags=["batches"])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _drain() -> None:
    """With no job worker running (tests, or a deployment without one),
    execute queued runs after the response instead."""
    await run_pending(kinds=["twin_run", "load_batch"], max_jobs=100000)


class PlanView(BaseModel):
    row_keys: list[str]
    estimate: service.Estimate


@router.post("/scenarios/{scenario_id}/batches/estimate", response_model=PlanView)
def estimate_batch(process_id: str, scenario_id: str, body: service.BatchRequest, db: DbDep, user: AgentTestDep) -> PlanView:
    """US34.3: what a batch would cost and take, before starting it."""
    del user
    try:
        row_keys, est, _ = service.plan(db, process_id, scenario_id, body)
    except service.BatchError as exc:
        raise _bad(exc) from exc
    return PlanView(row_keys=row_keys, estimate=est)


@router.post(
    "/scenarios/{scenario_id}/batches", response_model=service.BatchView, status_code=status.HTTP_202_ACCEPTED
)
def start_batch(
    process_id: str, scenario_id: str, body: service.BatchRequest, db: DbDep, user: AgentTestDep, background_tasks: BackgroundTasks
) -> service.BatchView:
    try:
        batch = service.start_batch(db, process_id, scenario_id, body, created_by=user.id)
    except (service.BatchError, TwinServiceError) as exc:
        raise _bad(exc) from exc
    db.commit()
    if not get_settings().job_worker_enabled:
        background_tasks.add_task(_drain)
    return service.to_view(db, batch)


@router.get("/batches", response_model=list[service.BatchView])
def list_batches(process_id: str, db: DbDep, user: CurrentUserDep, scenario_id: str | None = None) -> list[service.BatchView]:
    del user
    query = select(BatchRunModel).where(BatchRunModel.process_id == process_id).order_by(BatchRunModel.created_at.desc()).limit(50)
    if scenario_id:
        query = query.where(BatchRunModel.scenario_id == scenario_id)
    return [service.to_view(db, b) for b in db.scalars(query)]


@router.get("/batches/{batch_id}", response_model=service.BatchView)
def get_batch(process_id: str, batch_id: str, db: DbDep, user: CurrentUserDep) -> service.BatchView:
    del user
    return service.to_view(db, service.get_batch(db, process_id, batch_id))


class BatchAction(BaseModel):
    action: Literal["pause", "resume", "cancel"]


@router.post("/batches/{batch_id}/control", response_model=service.BatchView)
def control_batch(
    process_id: str, batch_id: str, body: BatchAction, db: DbDep, user: AgentTestDep, background_tasks: BackgroundTasks
) -> service.BatchView:
    """US34.7."""
    del user
    batch = service.get_batch(db, process_id, batch_id)
    try:
        service.set_status(db, batch, body.action)
    except service.BatchError as exc:
        raise _bad(exc) from exc
    db.commit()
    if body.action == "resume" and not get_settings().job_worker_enabled:
        background_tasks.add_task(_drain)
    return service.to_view(db, batch)


class ReplayView(BaseModel):
    run_id: str


@router.post("/batches/{batch_id}/runs/{run_id}/replay", response_model=ReplayView, status_code=status.HTTP_202_ACCEPTED)
def replay_run(
    process_id: str, batch_id: str, run_id: str, db: DbDep, user: AgentTestDep, background_tasks: BackgroundTasks
) -> ReplayView:
    """US34.6: the same row, trial, and seed -- on its own."""
    from app.connectors.jobs import get_job_llm
    from app.db import repository

    batch = service.get_batch(db, process_id, batch_id)
    try:
        new_run_id = service.replay(db, batch, run_id, run_by=user.id)
    except TwinServiceError as exc:
        raise _bad(exc) from exc
    db.commit()
    background_tasks.add_task(repository.execute_twin_run_background, new_run_id, get_job_llm())
    return ReplayView(run_id=new_run_id)


# ---- schedules (US34.9) ---------------------------------------------------


class ScheduleView(service.ScheduleCreate):
    id: str
    last_run_at: object = None


def _schedule_view(s: BatchScheduleModel) -> ScheduleView:
    return ScheduleView(
        id=s.id, scenario_id=s.scenario_id, tier=s.tier, trials=s.trials, every_minutes=s.every_minutes,  # type: ignore[arg-type]
        on_dataset_change=s.on_dataset_change, on_context_change=s.on_context_change, budget_usd=s.budget_usd,
        enabled=s.enabled, last_run_at=s.last_run_at,
    )


@router.get("/batch-schedules", response_model=list[ScheduleView])
def list_schedules(process_id: str, db: DbDep, user: CurrentUserDep) -> list[ScheduleView]:
    del user
    return [_schedule_view(s) for s in db.scalars(select(BatchScheduleModel).where(BatchScheduleModel.process_id == process_id))]


@router.post("/batch-schedules", response_model=ScheduleView, status_code=status.HTTP_201_CREATED)
def create_schedule(process_id: str, body: service.ScheduleCreate, db: DbDep, user: AgentTestDep) -> ScheduleView:
    del user
    scenario = db.get(TwinScenarioModel, body.scenario_id)
    if scenario is None or not scenario.dataset_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick a data-driven scenario (one with a dataset)")
    artifact = db.get(AgentArtifactModel, scenario.agent_artifact_id)
    if artifact is None or artifact.process_id != process_id:
        raise NotFoundError("scenario", body.scenario_id)
    dataset = db.get(LabelledDatasetModel, scenario.dataset_id)
    schedule = BatchScheduleModel(
        id=new_id("sched"), process_id=process_id, **body.model_dump(),
        # Baselines: a later change to either is what triggers a run.
        last_dataset_version=dataset.version if dataset else None,
        last_context_pack_id=artifact.context_pack_id,
    )
    db.add(schedule)
    db.flush()
    return _schedule_view(schedule)


@router.delete("/batch-schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(process_id: str, schedule_id: str, db: DbDep, user: AgentTestDep) -> None:
    del user
    schedule = db.get(BatchScheduleModel, schedule_id)
    if schedule is None or schedule.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"schedule '{schedule_id}' not found")
    db.delete(schedule)
    db.flush()
