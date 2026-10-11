"""Epic 31, US31.1: a durable job queue on Postgres.

Why Postgres and not Redis/RQ/Arq: the stack already runs Postgres and
nothing else, and `SELECT ... FOR UPDATE SKIP LOCKED` gives safe
multi-worker claiming. Jobs survive restarts because they're rows; a job
whose worker died mid-run is requeued once its lease expires.

Lifecycle: queued -> running -> succeeded | failed (after `max_attempts`,
with exponential backoff between attempts) | cancelled.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import JobModel
from app.ids import new_id, utcnow

ACTIVE_STATUSES = ("queued", "running")


def _now():
    # Columns are naive UTC (server_default now() on a UTC database).
    return utcnow().replace(tzinfo=None)


def enqueue(
    session: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    max_attempts: int = 3,
    priority: int = 100,
    delay_seconds: float = 0,
    dedupe_key: str | None = None,
) -> JobModel:
    """Adds a job. With `dedupe_key`, returns the existing queued/running
    job for that key instead of adding a second one."""
    if dedupe_key is not None:
        existing = session.scalar(
            select(JobModel).where(JobModel.dedupe_key == dedupe_key, JobModel.status.in_(ACTIVE_STATUSES))
        )
        if existing is not None:
            return existing
    job = JobModel(
        id=new_id("job"),
        kind=kind,
        payload=payload or {},
        status="queued",
        priority=priority,
        max_attempts=max_attempts,
        run_after=_now() + timedelta(seconds=delay_seconds),
        dedupe_key=dedupe_key,
    )
    session.add(job)
    session.flush()
    return job


def claim(session: Session, worker_id: str, *, kinds: list[str], limit: int) -> list[JobModel]:
    """Claims up to `limit` due jobs of the given kinds. Commit right after,
    so the claim is visible to other workers."""
    if limit <= 0 or not kinds:
        return []
    jobs = list(
        session.scalars(
            select(JobModel)
            .where(JobModel.status == "queued", JobModel.run_after <= _now(), JobModel.kind.in_(kinds))
            .order_by(JobModel.priority, JobModel.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        job.status = "running"
        job.locked_by = worker_id
        job.locked_at = _now()
        job.attempts += 1
    session.flush()
    return jobs


def complete(session: Session, job: JobModel, result: dict[str, Any] | None = None) -> None:
    job.status = "succeeded"
    job.result = result
    job.finished_at = _now()
    job.last_error = None
    session.flush()


def fail(session: Session, job: JobModel, error: str, *, backoff_base_seconds: float = 5) -> None:
    """Retries with exponential backoff until attempts run out."""
    job.last_error = error[:4000]
    job.locked_by = None
    job.locked_at = None
    if job.attempts < job.max_attempts:
        job.status = "queued"
        job.run_after = _now() + timedelta(seconds=backoff_base_seconds * (2 ** (job.attempts - 1)))
    else:
        job.status = "failed"
        job.finished_at = _now()
    session.flush()


def recover_expired(session: Session, *, lease_seconds: float) -> int:
    """Requeues running jobs whose worker stopped renewing them -- what
    makes jobs survive a restart mid-run."""
    cutoff = _now() - timedelta(seconds=lease_seconds)
    jobs = list(
        session.scalars(
            select(JobModel).where(
                JobModel.status == "running", or_(JobModel.locked_at.is_(None), JobModel.locked_at < cutoff)
            )
        )
    )
    for job in jobs:
        job.status = "queued" if job.attempts < job.max_attempts else "failed"
        job.locked_by = None
        job.locked_at = None
        job.last_error = "Worker stopped before finishing (lease expired)"
        if job.status == "failed":
            job.finished_at = _now()
    session.flush()
    return len(jobs)


def cancel(session: Session, job_id: str) -> JobModel | None:
    job = session.get(JobModel, job_id)
    if job is not None and job.status == "queued":
        job.status = "cancelled"
        job.finished_at = _now()
        session.flush()
    return job
