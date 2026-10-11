"""Epic 31, US31.1: job handlers for connector syncs and twin runs, and
the scheduler that queues syncs when they're due."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ConnectorModel
from app.ids import utcnow
from app.jobs import queue
from app.jobs.worker import handler


def get_job_llm():
    """The LLM client jobs use: whatever the API itself is configured to
    use -- including a FastAPI dependency override -- so a job runs with
    the same client as the request that queued it."""
    from app.llm import get_llm_client
    from app.main import app

    return app.dependency_overrides.get(get_llm_client, get_llm_client)()


@handler("connector_sync")
async def connector_sync(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from .sync import run_sync

    connector = session.get(ConnectorModel, payload["connector_id"])
    if connector is None:
        return {"skipped": "connector deleted"}
    run = await run_sync(session, connector)
    if run.status == "failed":
        # Recorded on the connector and its run; raising makes the queue retry.
        session.commit()
        raise RuntimeError(run.error or "sync failed")
    return {"run_id": run.id, "stats": run.stats}


@handler("twin_run")
async def twin_run(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    """Executes a twin run created elsewhere (a suite re-run, a batch row).
    The run function manages its own session. Epic 34: a batch run first
    checks its batch -- paused waits, cancelled or over-budget is skipped
    -- and the last run to finish completes the batch."""
    from app.batches import service as batches
    from app.db import repository
    from app.db.models import SimulationRunModel

    run = session.get(SimulationRunModel, payload["run_id"])
    batch_id = run.batch_id if run is not None else None
    if run is not None and batch_id:
        decision = batches.gate_run(session, run)
        session.commit()
        if decision == "wait":
            queue.enqueue(session, "twin_run", payload, delay_seconds=5, max_attempts=2)
            return {"run_id": run.id, "waiting": True}
        if decision == "skip":
            batches.finish_if_done(session, batch_id)
            return {"run_id": run.id, "skipped": True}
    await repository.execute_twin_run_background(payload["run_id"], get_job_llm())
    if batch_id:
        session.expire_all()
        batches.finish_if_done(session, batch_id)
    return {"run_id": payload["run_id"]}


@handler("load_batch")
async def load_batch(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    """Epic 34, US34.10: drive a load batch's runs at its target
    concurrency in this process, so latency under concurrent load is what
    gets measured (the queue's own concurrency limit would flatten it)."""
    import asyncio

    from app.batches import service as batches
    from app.db import repository
    from app.db.models import BatchRunModel

    batch = session.get(BatchRunModel, payload["batch_id"])
    if batch is None:
        return {"skipped": "batch deleted"}
    semaphore = asyncio.Semaphore(int(batch.load_settings.get("target_concurrency", 4)))
    run_ids = [r.id for r in batches.batch_runs(session, batch.id) if r.status == "CREATED"]
    llm = get_job_llm()

    async def one(run_id: str) -> None:
        async with semaphore:
            await repository.execute_twin_run_background(run_id, llm)

    await asyncio.gather(*(one(run_id) for run_id in run_ids))
    session.expire_all()
    batches.finish_if_done(session, batch.id)
    return {"runs": len(run_ids)}


@handler("orchestration_run")
async def orchestration_run(payload: dict[str, Any], session: Session) -> dict[str, Any]:
    from app.db import repository

    await repository.execute_orchestration_run_background(payload["run_id"], get_job_llm())
    return {"run_id": payload["run_id"]}


def enqueue_due_syncs(session: Session) -> int:
    """Queues a sync for every enabled, scheduled connector whose interval
    has passed. The dedupe key keeps it to one pending sync per connector."""
    now = utcnow().replace(tzinfo=None)
    count = 0
    for connector in session.scalars(select(ConnectorModel).where(ConnectorModel.enabled.is_(True))):
        if not connector.schedule_minutes:
            continue
        due = connector.last_sync_at is None or connector.last_sync_at + timedelta(minutes=connector.schedule_minutes) <= now
        if due:
            queue.enqueue(session, "connector_sync", {"connector_id": connector.id}, dedupe_key=f"sync:{connector.id}")
            count += 1
    session.flush()
    return count
