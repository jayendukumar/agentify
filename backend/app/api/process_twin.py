"""Epic 36: the process digital twin -- the as-is operating model, running
the whole process over a dataset, and the efficiency report."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, status
from pydantic import BaseModel

from app.config import get_settings
from app.jobs.worker import run_pending
from app.process_twin import service
from app.schemas.process_twin import (
    EfficiencyReport,
    OperatingModel,
    OperatingModelView,
    ProcessTwinBatchView,
    ProcessTwinEstimate,
    ProcessTwinRequest,
)

from .deps import AgentTestDep, CurrentUserDep, DbDep, ProcessScope

router = APIRouter(prefix="/api/processes/{process_id}/process-twin", tags=["process-twin"], dependencies=[ProcessScope])


def _bad(exc: Exception) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _drain() -> None:
    """With no job worker running (tests, or a deployment without one),
    run the queued cases after the response instead."""
    await run_pending(kinds=[service.JOB_KIND], max_jobs=100000)


@router.get("/operating-model", response_model=OperatingModelView)
def get_operating_model(process_id: str, db: DbDep, user: CurrentUserDep) -> OperatingModelView:
    """US36.4: the saved model, or one pre-filled from the diagram."""
    del user
    try:
        return service.get_operating_model(db, process_id)
    except service.ProcessTwinError as exc:
        raise _bad(exc) from exc


@router.put("/operating-model", response_model=OperatingModelView)
def save_operating_model(process_id: str, body: OperatingModel, db: DbDep, user: AgentTestDep) -> OperatingModelView:
    view = service.save_operating_model(db, process_id, body, user_id=user.id)
    db.commit()
    return view


@router.post("/estimate", response_model=ProcessTwinEstimate)
def estimate(process_id: str, body: ProcessTwinRequest, db: DbDep, user: AgentTestDep) -> ProcessTwinEstimate:
    del user
    try:
        return service.estimate(db, process_id, body)
    except service.ProcessTwinError as exc:
        raise _bad(exc) from exc


@router.post("/batches", response_model=ProcessTwinBatchView, status_code=status.HTTP_202_ACCEPTED)
def start(
    process_id: str, body: ProcessTwinRequest, db: DbDep, user: AgentTestDep, background_tasks: BackgroundTasks
) -> ProcessTwinBatchView:
    """US36.5: one case per dataset row, through every agent."""
    try:
        batch = service.start(db, process_id, body, created_by=user.id)
    except service.ProcessTwinError as exc:
        raise _bad(exc) from exc
    db.commit()
    if not get_settings().job_worker_enabled:
        background_tasks.add_task(_drain)
    return service.to_view(db, batch)


@router.get("/batches", response_model=list[ProcessTwinBatchView])
def list_batches(process_id: str, db: DbDep, user: CurrentUserDep) -> list[ProcessTwinBatchView]:
    del user
    return service.list_batches(db, process_id)


@router.get("/batches/{batch_id}", response_model=ProcessTwinBatchView)
def get_batch(process_id: str, batch_id: str, db: DbDep, user: CurrentUserDep) -> ProcessTwinBatchView:
    del user
    return service.to_view(db, service.get_batch(db, process_id, batch_id))


class BatchAction(BaseModel):
    action: Literal["pause", "resume", "cancel"]


@router.post("/batches/{batch_id}/control", response_model=ProcessTwinBatchView)
def control(
    process_id: str, batch_id: str, body: BatchAction, db: DbDep, user: AgentTestDep, background_tasks: BackgroundTasks
) -> ProcessTwinBatchView:
    del user
    batch = service.get_batch(db, process_id, batch_id)
    try:
        service.set_status(db, batch, body.action)
    except service.ProcessTwinError as exc:
        raise _bad(exc) from exc
    db.commit()
    if body.action == "resume" and not get_settings().job_worker_enabled:
        background_tasks.add_task(_drain)
    return service.to_view(db, batch)


@router.get("/batches/{batch_id}/report", response_model=EfficiencyReport)
def report(process_id: str, batch_id: str, db: DbDep, user: CurrentUserDep) -> EfficiencyReport:
    """US36.7: live while running, frozen once finished."""
    del user
    try:
        return service.report(db, service.get_batch(db, process_id, batch_id))
    except service.ProcessTwinError as exc:
        raise _bad(exc) from exc
