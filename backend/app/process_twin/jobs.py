"""Epic 36, US36.5: the job that runs one process twin case."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.connectors.jobs import get_job_llm
from app.db import repository
from app.db.models import SimulationRunModel
from app.jobs import queue
from app.jobs.worker import handler

from . import service
from .service import JOB_KIND


@handler(JOB_KIND)
async def process_twin_run(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    """Gate the case on its batch (pause waits, cancel or budget skips),
    run it, and let the last case to finish complete the batch."""
    run = session.get(SimulationRunModel, payload["run_id"])
    if run is None:
        return {"skipped": "run deleted"}
    decision = service.gate_run(session, run)
    session.commit()
    if decision == "wait":
        queue.enqueue(session, JOB_KIND, payload, delay_seconds=5, max_attempts=1)
        return {"run_id": run.id, "waiting": True}
    batch_id = run.batch_id
    if decision == "go":
        await repository.execute_orchestration_run_background(run.id, get_job_llm())
    session.expire_all()
    service.finish_if_done(session, batch_id)
    return {"run_id": payload["run_id"], "skipped": decision == "skip"}
